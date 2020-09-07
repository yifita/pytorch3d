# Copyright (c) Facebook, Inc. and its affiliates. All rights reserved.


"""
Sanity checks for output images from the renderer.
"""
import os
import unittest
from pathlib import Path

import numpy as np
import torch
from common_testing import TestCaseMixin, load_rgb_image
from PIL import Image
from pytorch3d.io import load_obj
from pytorch3d.renderer.cameras import (
    FoVOrthographicCameras,
    FoVPerspectiveCameras,
    OrthographicCameras,
    PerspectiveCameras,
    look_at_view_transform,
)
from pytorch3d.renderer.lighting import PointLights
from pytorch3d.renderer.materials import Materials
from pytorch3d.renderer.mesh import TexturesAtlas, TexturesUV, TexturesVertex
from pytorch3d.renderer.mesh.rasterizer import MeshRasterizer, RasterizationSettings
from pytorch3d.renderer.mesh.renderer import MeshRenderer
from pytorch3d.renderer.mesh.shader import (
    BlendParams,
    HardFlatShader,
    HardGouraudShader,
    HardPhongShader,
    SoftSilhouetteShader,
    TexturedSoftPhongShader,
)
from pytorch3d.structures.meshes import (
    Meshes,
    join_meshes_as_batch,
    join_meshes_as_scene,
)
from pytorch3d.utils.ico_sphere import ico_sphere
from pytorch3d.utils.torus import torus


# If DEBUG=True, save out images generated in the tests for debugging.
# All saved images have prefix DEBUG_
DEBUG = False
DATA_DIR = Path(__file__).resolve().parent / "data"


class TestRenderMeshes(TestCaseMixin, unittest.TestCase):
    def test_simple_sphere(self, elevated_camera=False):
        """
        Test output of phong and gouraud shading matches a reference image using
        the default values for the light sources.

        Args:
            elevated_camera: Defines whether the camera observing the scene should
                           have an elevation of 45 degrees.
        """
        device = torch.device("cuda:0")

        # Init mesh
        sphere_mesh = ico_sphere(5, device)
        verts_padded = sphere_mesh.verts_padded()
        faces_padded = sphere_mesh.faces_padded()
        feats = torch.ones_like(verts_padded, device=device)
        textures = TexturesVertex(verts_features=feats)
        sphere_mesh = Meshes(verts=verts_padded, faces=faces_padded, textures=textures)

        # Init rasterizer settings
        if elevated_camera:
            # Elevated and rotated camera
            R, T = look_at_view_transform(dist=2.7, elev=45.0, azim=45.0)
            postfix = "_elevated_"
            # If y axis is up, the spot of light should
            # be on the bottom left of the sphere.
        else:
            # No elevation or azimuth rotation
            R, T = look_at_view_transform(2.7, 0.0, 0.0)
            postfix = "_"
        for cam_type in (
            FoVPerspectiveCameras,
            FoVOrthographicCameras,
            PerspectiveCameras,
            OrthographicCameras,
        ):
            cameras = cam_type(device=device, R=R, T=T)

            # Init shader settings
            materials = Materials(device=device)
            lights = PointLights(device=device)
            lights.location = torch.tensor([0.0, 0.0, +2.0], device=device)[None]

            raster_settings = RasterizationSettings(
                image_size=512, blur_radius=0.0, faces_per_pixel=1
            )
            rasterizer = MeshRasterizer(
                cameras=cameras, raster_settings=raster_settings
            )
            blend_params = BlendParams(1e-4, 1e-4, (0, 0, 0))

            # Test several shaders
            shaders = {
                "phong": HardPhongShader,
                "gouraud": HardGouraudShader,
                "flat": HardFlatShader,
            }
            for (name, shader_init) in shaders.items():
                shader = shader_init(
                    lights=lights,
                    cameras=cameras,
                    materials=materials,
                    blend_params=blend_params,
                )
                renderer = MeshRenderer(rasterizer=rasterizer, shader=shader)
                images = renderer(sphere_mesh)
                rgb = images[0, ..., :3].squeeze().cpu()
                filename = "simple_sphere_light_%s%s%s.png" % (
                    name,
                    postfix,
                    cam_type.__name__,
                )

                image_ref = load_rgb_image("test_%s" % filename, DATA_DIR)
                self.assertClose(rgb, image_ref, atol=0.05)

                if DEBUG:
                    filename = "DEBUG_%s" % filename
                    Image.fromarray((rgb.numpy() * 255).astype(np.uint8)).save(
                        DATA_DIR / filename
                    )

            ########################################################
            # Move the light to the +z axis in world space so it is
            # behind the sphere. Note that +Z is in, +Y up,
            # +X left for both world and camera space.
            ########################################################
            lights.location[..., 2] = -2.0
            phong_shader = HardPhongShader(
                lights=lights,
                cameras=cameras,
                materials=materials,
                blend_params=blend_params,
            )
            phong_renderer = MeshRenderer(rasterizer=rasterizer, shader=phong_shader)
            images = phong_renderer(sphere_mesh, lights=lights)
            rgb = images[0, ..., :3].squeeze().cpu()
            if DEBUG:
                filename = "DEBUG_simple_sphere_dark%s%s.png" % (
                    postfix,
                    cam_type.__name__,
                )
                Image.fromarray((rgb.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / filename
                )

            image_ref_phong_dark = load_rgb_image(
                "test_simple_sphere_dark%s%s.png" % (postfix, cam_type.__name__),
                DATA_DIR,
            )
            self.assertClose(rgb, image_ref_phong_dark, atol=0.05)

    def test_simple_sphere_elevated_camera(self):
        """
        Test output of phong and gouraud shading matches a reference image using
        the default values for the light sources.

        The rendering is performed with a camera that has non-zero elevation.
        """
        self.test_simple_sphere(elevated_camera=True)

    def test_simple_sphere_screen(self):

        """
        Test output when rendering with PerspectiveCameras & OrthographicCameras
        in NDC vs screen space.
        """
        device = torch.device("cuda:0")

        # Init mesh
        sphere_mesh = ico_sphere(5, device)
        verts_padded = sphere_mesh.verts_padded()
        faces_padded = sphere_mesh.faces_padded()
        feats = torch.ones_like(verts_padded, device=device)
        textures = TexturesVertex(verts_features=feats)
        sphere_mesh = Meshes(verts=verts_padded, faces=faces_padded, textures=textures)

        R, T = look_at_view_transform(2.7, 0.0, 0.0)

        # Init shader settings
        materials = Materials(device=device)
        lights = PointLights(device=device)
        lights.location = torch.tensor([0.0, 0.0, +2.0], device=device)[None]

        raster_settings = RasterizationSettings(
            image_size=512, blur_radius=0.0, faces_per_pixel=1
        )
        for cam_type in (PerspectiveCameras, OrthographicCameras):
            cameras = cam_type(
                device=device,
                R=R,
                T=T,
                principal_point=((256.0, 256.0),),
                focal_length=((256.0, 256.0),),
                image_size=((512, 512),),
            )
            rasterizer = MeshRasterizer(
                cameras=cameras, raster_settings=raster_settings
            )
            blend_params = BlendParams(1e-4, 1e-4, (0, 0, 0))

            shader = HardPhongShader(
                lights=lights,
                cameras=cameras,
                materials=materials,
                blend_params=blend_params,
            )
            renderer = MeshRenderer(rasterizer=rasterizer, shader=shader)
            images = renderer(sphere_mesh)
            rgb = images[0, ..., :3].squeeze().cpu()
            filename = "test_simple_sphere_light_phong_%s.png" % cam_type.__name__

            image_ref = load_rgb_image(filename, DATA_DIR)
            self.assertClose(rgb, image_ref, atol=0.05)

    def test_simple_sphere_batched(self):
        """
        Test a mesh with vertex textures can be extended to form a batch, and
        is rendered correctly with Phong, Gouraud and Flat Shaders.
        """
        batch_size = 5
        device = torch.device("cuda:0")

        # Init mesh with vertex textures.
        sphere_meshes = ico_sphere(5, device).extend(batch_size)
        verts_padded = sphere_meshes.verts_padded()
        faces_padded = sphere_meshes.faces_padded()
        feats = torch.ones_like(verts_padded, device=device)
        textures = TexturesVertex(verts_features=feats)
        sphere_meshes = Meshes(
            verts=verts_padded, faces=faces_padded, textures=textures
        )

        # Init rasterizer settings
        dist = torch.tensor([2.7]).repeat(batch_size).to(device)
        elev = torch.zeros_like(dist)
        azim = torch.zeros_like(dist)
        R, T = look_at_view_transform(dist, elev, azim)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)
        raster_settings = RasterizationSettings(
            image_size=512, blur_radius=0.0, faces_per_pixel=1
        )

        # Init shader settings
        materials = Materials(device=device)
        lights = PointLights(device=device)
        lights.location = torch.tensor([0.0, 0.0, +2.0], device=device)[None]
        blend_params = BlendParams(1e-4, 1e-4, (0, 0, 0))

        # Init renderer
        rasterizer = MeshRasterizer(cameras=cameras, raster_settings=raster_settings)
        shaders = {
            "phong": HardPhongShader,
            "gouraud": HardGouraudShader,
            "flat": HardFlatShader,
        }
        for (name, shader_init) in shaders.items():
            shader = shader_init(
                lights=lights,
                cameras=cameras,
                materials=materials,
                blend_params=blend_params,
            )
            renderer = MeshRenderer(rasterizer=rasterizer, shader=shader)
            images = renderer(sphere_meshes)
            image_ref = load_rgb_image(
                "test_simple_sphere_light_%s_%s.png" % (name, type(cameras).__name__),
                DATA_DIR,
            )
            for i in range(batch_size):
                rgb = images[i, ..., :3].squeeze().cpu()
                if i == 0 and DEBUG:
                    filename = "DEBUG_simple_sphere_batched_%s_%s.png" % (
                        name,
                        type(cameras).__name__,
                    )
                    Image.fromarray((rgb.numpy() * 255).astype(np.uint8)).save(
                        DATA_DIR / filename
                    )
                self.assertClose(rgb, image_ref, atol=0.05)

    def test_silhouette_with_grad(self):
        """
        Test silhouette blending. Also check that gradient calculation works.
        """
        device = torch.device("cuda:0")
        sphere_mesh = ico_sphere(5, device)
        verts, faces = sphere_mesh.get_mesh_verts_faces(0)
        sphere_mesh = Meshes(verts=[verts], faces=[faces])

        blend_params = BlendParams(sigma=1e-4, gamma=1e-4)
        raster_settings = RasterizationSettings(
            image_size=512,
            blur_radius=np.log(1.0 / 1e-4 - 1.0) * blend_params.sigma,
            faces_per_pixel=80,
            clip_barycentric_coords=True,
        )

        # Init rasterizer settings
        R, T = look_at_view_transform(2.7, 0, 0)
        for cam_type in (
            FoVPerspectiveCameras,
            FoVOrthographicCameras,
            PerspectiveCameras,
            OrthographicCameras,
        ):
            cameras = cam_type(device=device, R=R, T=T)

            # Init renderer
            renderer = MeshRenderer(
                rasterizer=MeshRasterizer(
                    cameras=cameras, raster_settings=raster_settings
                ),
                shader=SoftSilhouetteShader(blend_params=blend_params),
            )
            images = renderer(sphere_mesh)
            alpha = images[0, ..., 3].squeeze().cpu()
            if DEBUG:
                filename = os.path.join(
                    DATA_DIR, "DEBUG_%s_silhouette.png" % (cam_type.__name__)
                )
                Image.fromarray((alpha.detach().numpy() * 255).astype(np.uint8)).save(
                    filename
                )

            ref_filename = "test_%s_silhouette.png" % (cam_type.__name__)
            image_ref_filename = DATA_DIR / ref_filename
            with Image.open(image_ref_filename) as raw_image_ref:
                image_ref = torch.from_numpy(np.array(raw_image_ref))

            image_ref = image_ref.to(dtype=torch.float32) / 255.0
            self.assertClose(alpha, image_ref, atol=0.055)

            # Check grad exist
            verts.requires_grad = True
            sphere_mesh = Meshes(verts=[verts], faces=[faces])
            images = renderer(sphere_mesh)
            images[0, ...].sum().backward()
            self.assertIsNotNone(verts.grad)

    def test_texture_map(self):
        """
        Test a mesh with a texture map is loaded and rendered correctly.
        The pupils in the eyes of the cow should always be looking to the left.
        """
        device = torch.device("cuda:0")
        obj_dir = Path(__file__).resolve().parent.parent / "docs/tutorials/data"
        obj_filename = obj_dir / "cow_mesh/cow.obj"

        # Load mesh + texture
        verts, faces, aux = load_obj(
            obj_filename, device=device, load_textures=True, texture_wrap=None
        )
        tex_map = list(aux.texture_images.values())[0]
        tex_map = tex_map[None, ...].to(faces.textures_idx.device)
        textures = TexturesUV(
            maps=tex_map, faces_uvs=[faces.textures_idx], verts_uvs=[aux.verts_uvs]
        )
        mesh = Meshes(verts=[verts], faces=[faces.verts_idx], textures=textures)

        # Init rasterizer settings
        R, T = look_at_view_transform(2.7, 0, 0)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)

        raster_settings = RasterizationSettings(
            image_size=512, blur_radius=0.0, faces_per_pixel=1
        )

        # Init shader settings
        materials = Materials(device=device)
        lights = PointLights(device=device)

        # Place light behind the cow in world space. The front of
        # the cow is facing the -z direction.
        lights.location = torch.tensor([0.0, 0.0, 2.0], device=device)[None]

        blend_params = BlendParams(
            sigma=1e-1,
            gamma=1e-4,
            background_color=torch.tensor([1.0, 1.0, 1.0], device=device),
        )
        # Init renderer
        renderer = MeshRenderer(
            rasterizer=MeshRasterizer(cameras=cameras, raster_settings=raster_settings),
            shader=TexturedSoftPhongShader(
                lights=lights,
                cameras=cameras,
                materials=materials,
                blend_params=blend_params,
            ),
        )

        # Load reference image
        image_ref = load_rgb_image("test_texture_map_back.png", DATA_DIR)

        for bin_size in [0, None]:
            # Check both naive and coarse to fine produce the same output.
            renderer.rasterizer.raster_settings.bin_size = bin_size
            images = renderer(mesh)
            rgb = images[0, ..., :3].squeeze().cpu()

            if DEBUG:
                Image.fromarray((rgb.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / "DEBUG_texture_map_back.png"
                )

            # NOTE some pixels can be flaky and will not lead to
            # `cond1` being true. Add `cond2` and check `cond1 or cond2`
            cond1 = torch.allclose(rgb, image_ref, atol=0.05)
            cond2 = ((rgb - image_ref).abs() > 0.05).sum() < 5
            self.assertTrue(cond1 or cond2)

        # Check grad exists
        [verts] = mesh.verts_list()
        verts.requires_grad = True
        mesh2 = Meshes(verts=[verts], faces=mesh.faces_list(), textures=mesh.textures)
        images = renderer(mesh2)
        images[0, ...].sum().backward()
        self.assertIsNotNone(verts.grad)

        ##########################################
        # Check rendering of the front of the cow
        ##########################################

        R, T = look_at_view_transform(2.7, 0, 180)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)

        # Move light to the front of the cow in world space
        lights.location = torch.tensor([0.0, 0.0, -2.0], device=device)[None]

        # Load reference image
        image_ref = load_rgb_image("test_texture_map_front.png", DATA_DIR)

        for bin_size in [0, None]:
            # Check both naive and coarse to fine produce the same output.
            renderer.rasterizer.raster_settings.bin_size = bin_size

            images = renderer(mesh, cameras=cameras, lights=lights)
            rgb = images[0, ..., :3].squeeze().cpu()

            if DEBUG:
                Image.fromarray((rgb.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / "DEBUG_texture_map_front.png"
                )

            # NOTE some pixels can be flaky and will not lead to
            # `cond1` being true. Add `cond2` and check `cond1 or cond2`
            cond1 = torch.allclose(rgb, image_ref, atol=0.05)
            cond2 = ((rgb - image_ref).abs() > 0.05).sum() < 5
            self.assertTrue(cond1 or cond2)

        #################################
        # Add blurring to rasterization
        #################################
        R, T = look_at_view_transform(2.7, 0, 180)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)
        blend_params = BlendParams(sigma=5e-4, gamma=1e-4)
        raster_settings = RasterizationSettings(
            image_size=512,
            blur_radius=np.log(1.0 / 1e-4 - 1.0) * blend_params.sigma,
            faces_per_pixel=100,
            clip_barycentric_coords=True,
        )

        # Load reference image
        image_ref = load_rgb_image("test_blurry_textured_rendering.png", DATA_DIR)

        for bin_size in [0, None]:
            # Check both naive and coarse to fine produce the same output.
            renderer.rasterizer.raster_settings.bin_size = bin_size

            images = renderer(
                mesh.clone(),
                cameras=cameras,
                raster_settings=raster_settings,
                blend_params=blend_params,
            )
            rgb = images[0, ..., :3].squeeze().cpu()

            if DEBUG:
                Image.fromarray((rgb.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / "DEBUG_blurry_textured_rendering.png"
                )

            self.assertClose(rgb, image_ref, atol=0.05)

    def test_batch_uvs(self):
        """Test that two random tori with TexturesUV render the same as each individually."""
        torch.manual_seed(1)
        device = torch.device("cuda:0")
        plain_torus = torus(r=1, R=4, sides=10, rings=10, device=device)
        [verts] = plain_torus.verts_list()
        [faces] = plain_torus.faces_list()
        nocolor = torch.zeros((100, 100), device=device)
        color_gradient = torch.linspace(0, 1, steps=100, device=device)
        color_gradient1 = color_gradient[None].expand_as(nocolor)
        color_gradient2 = color_gradient[:, None].expand_as(nocolor)
        colors1 = torch.stack([nocolor, color_gradient1, color_gradient2], dim=2)
        colors2 = torch.stack([color_gradient1, color_gradient2, nocolor], dim=2)
        verts_uvs1 = torch.rand(size=(verts.shape[0], 2), device=device)
        verts_uvs2 = torch.rand(size=(verts.shape[0], 2), device=device)

        textures1 = TexturesUV(
            maps=[colors1], faces_uvs=[faces], verts_uvs=[verts_uvs1]
        )
        textures2 = TexturesUV(
            maps=[colors2], faces_uvs=[faces], verts_uvs=[verts_uvs2]
        )
        mesh1 = Meshes(verts=[verts], faces=[faces], textures=textures1)
        mesh2 = Meshes(verts=[verts], faces=[faces], textures=textures2)
        mesh_both = join_meshes_as_batch([mesh1, mesh2])

        R, T = look_at_view_transform(10, 10, 0)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)

        raster_settings = RasterizationSettings(
            image_size=128, blur_radius=0.0, faces_per_pixel=1
        )

        # Init shader settings
        lights = PointLights(device=device)
        lights.location = torch.tensor([0.0, 0.0, 2.0], device=device)[None]

        blend_params = BlendParams(
            sigma=1e-1,
            gamma=1e-4,
            background_color=torch.tensor([1.0, 1.0, 1.0], device=device),
        )
        # Init renderer
        renderer = MeshRenderer(
            rasterizer=MeshRasterizer(cameras=cameras, raster_settings=raster_settings),
            shader=HardPhongShader(
                device=device, lights=lights, cameras=cameras, blend_params=blend_params
            ),
        )

        outputs = []
        for meshes in [mesh_both, mesh1, mesh2]:
            outputs.append(renderer(meshes))

        if DEBUG:
            Image.fromarray(
                (outputs[0][0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_batch_uvs0.png")
            Image.fromarray(
                (outputs[1][0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_batch_uvs1.png")
            Image.fromarray(
                (outputs[0][1, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_batch_uvs2.png")
            Image.fromarray(
                (outputs[2][0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_batch_uvs3.png")

            diff = torch.abs(outputs[0][0, ..., :3] - outputs[1][0, ..., :3])
            Image.fromarray(((diff > 1e-5).cpu().numpy().astype(np.uint8) * 255)).save(
                DATA_DIR / "test_batch_uvs01.png"
            )
            diff = torch.abs(outputs[0][1, ..., :3] - outputs[2][0, ..., :3])
            Image.fromarray(((diff > 1e-5).cpu().numpy().astype(np.uint8) * 255)).save(
                DATA_DIR / "test_batch_uvs23.png"
            )

        self.assertClose(outputs[0][0, ..., :3], outputs[1][0, ..., :3], atol=1e-5)
        self.assertClose(outputs[0][1, ..., :3], outputs[2][0, ..., :3], atol=1e-5)

    def test_join_uvs(self):
        """Meshes with TexturesUV joined into a scene"""
        # Test the result of rendering three tori with separate textures.
        # The expected result is consistent with rendering them each alone.
        # This tests TexturesUV.join_scene with rectangle flipping,
        # and we check the form of the merged map as well.
        torch.manual_seed(1)
        device = torch.device("cuda:0")

        R, T = look_at_view_transform(18, 0, 0)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)

        raster_settings = RasterizationSettings(
            image_size=256, blur_radius=0.0, faces_per_pixel=1
        )

        lights = PointLights(
            device=device,
            ambient_color=((1.0, 1.0, 1.0),),
            diffuse_color=((0.0, 0.0, 0.0),),
            specular_color=((0.0, 0.0, 0.0),),
        )
        blend_params = BlendParams(
            sigma=1e-1,
            gamma=1e-4,
            background_color=torch.tensor([1.0, 1.0, 1.0], device=device),
        )
        renderer = MeshRenderer(
            rasterizer=MeshRasterizer(cameras=cameras, raster_settings=raster_settings),
            shader=HardPhongShader(
                device=device, blend_params=blend_params, cameras=cameras, lights=lights
            ),
        )

        plain_torus = torus(r=1, R=4, sides=5, rings=6, device=device)
        [verts] = plain_torus.verts_list()
        verts_shifted1 = verts.clone()
        verts_shifted1 *= 0.5
        verts_shifted1[:, 1] += 7
        verts_shifted2 = verts.clone()
        verts_shifted2 *= 0.5
        verts_shifted2[:, 1] -= 7

        [faces] = plain_torus.faces_list()
        nocolor = torch.zeros((100, 100), device=device)
        color_gradient = torch.linspace(0, 1, steps=100, device=device)
        color_gradient1 = color_gradient[None].expand_as(nocolor)
        color_gradient2 = color_gradient[:, None].expand_as(nocolor)
        colors1 = torch.stack([nocolor, color_gradient1, color_gradient2], dim=2)
        colors2 = torch.stack([color_gradient1, color_gradient2, nocolor], dim=2)
        verts_uvs1 = torch.rand(size=(verts.shape[0], 2), device=device)
        verts_uvs2 = torch.rand(size=(verts.shape[0], 2), device=device)

        for i, align_corners, padding_mode in [
            (0, True, "border"),
            (1, False, "border"),
            (2, False, "zeros"),
        ]:
            textures1 = TexturesUV(
                maps=[colors1],
                faces_uvs=[faces],
                verts_uvs=[verts_uvs1],
                align_corners=align_corners,
                padding_mode=padding_mode,
            )

            # These downsamplings of colors2 are chosen to ensure a flip and a non flip
            # when the maps are merged.
            # We have maps of size (100, 100), (50, 99) and (99, 50).
            textures2 = TexturesUV(
                maps=[colors2[::2, :-1]],
                faces_uvs=[faces],
                verts_uvs=[verts_uvs2],
                align_corners=align_corners,
                padding_mode=padding_mode,
            )
            offset = torch.tensor([0, 0, 0.5], device=device)
            textures3 = TexturesUV(
                maps=[colors2[:-1, ::2] + offset],
                faces_uvs=[faces],
                verts_uvs=[verts_uvs2],
                align_corners=align_corners,
                padding_mode=padding_mode,
            )
            mesh1 = Meshes(verts=[verts], faces=[faces], textures=textures1)
            mesh2 = Meshes(verts=[verts_shifted1], faces=[faces], textures=textures2)
            mesh3 = Meshes(verts=[verts_shifted2], faces=[faces], textures=textures3)
            mesh = join_meshes_as_scene([mesh1, mesh2, mesh3])

            output = renderer(mesh)[0, ..., :3].cpu()
            output1 = renderer(mesh1)[0, ..., :3].cpu()
            output2 = renderer(mesh2)[0, ..., :3].cpu()
            output3 = renderer(mesh3)[0, ..., :3].cpu()
            # The background color is white and the objects do not overlap, so we can
            # predict the merged image by taking the minimum over every channel
            merged = torch.min(torch.min(output1, output2), output3)

            image_ref = load_rgb_image(f"test_joinuvs{i}_final.png", DATA_DIR)
            map_ref = load_rgb_image(f"test_joinuvs{i}_map.png", DATA_DIR)

            if DEBUG:
                Image.fromarray((output.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / f"test_joinuvs{i}_final_.png"
                )
                Image.fromarray((output.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / f"test_joinuvs{i}_merged.png"
                )

                Image.fromarray((output1.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / f"test_joinuvs{i}_1.png"
                )
                Image.fromarray((output2.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / f"test_joinuvs{i}_2.png"
                )
                Image.fromarray((output3.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / f"test_joinuvs{i}_3.png"
                )
                Image.fromarray(
                    (mesh.textures.maps_padded()[0].cpu().numpy() * 255).astype(
                        np.uint8
                    )
                ).save(DATA_DIR / f"test_joinuvs{i}_map_.png")
                Image.fromarray(
                    (mesh2.textures.maps_padded()[0].cpu().numpy() * 255).astype(
                        np.uint8
                    )
                ).save(DATA_DIR / f"test_joinuvs{i}_map2.png")
                Image.fromarray(
                    (mesh3.textures.maps_padded()[0].cpu().numpy() * 255).astype(
                        np.uint8
                    )
                ).save(DATA_DIR / f"test_joinuvs{i}_map3.png")

            self.assertClose(output, merged, atol=0.015)
            self.assertClose(output, image_ref, atol=0.05)
            self.assertClose(mesh.textures.maps_padded()[0].cpu(), map_ref, atol=0.05)

    def test_join_verts(self):
        """Meshes with TexturesVertex joined into a scene"""
        # Test the result of rendering two tori with separate textures.
        # The expected result is consistent with rendering them each alone.
        torch.manual_seed(1)
        device = torch.device("cuda:0")
        plain_torus = torus(r=1, R=4, sides=5, rings=6, device=device)
        [verts] = plain_torus.verts_list()
        verts_shifted1 = verts.clone()
        verts_shifted1 *= 0.5
        verts_shifted1[:, 1] += 7

        faces = plain_torus.faces_list()
        textures1 = TexturesVertex(verts_features=[torch.rand_like(verts)])
        textures2 = TexturesVertex(verts_features=[torch.rand_like(verts)])
        mesh1 = Meshes(verts=[verts], faces=faces, textures=textures1)
        mesh2 = Meshes(verts=[verts_shifted1], faces=faces, textures=textures2)
        mesh = join_meshes_as_scene([mesh1, mesh2])

        R, T = look_at_view_transform(18, 0, 0)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)

        raster_settings = RasterizationSettings(
            image_size=256, blur_radius=0.0, faces_per_pixel=1
        )

        lights = PointLights(
            device=device,
            ambient_color=((1.0, 1.0, 1.0),),
            diffuse_color=((0.0, 0.0, 0.0),),
            specular_color=((0.0, 0.0, 0.0),),
        )
        blend_params = BlendParams(
            sigma=1e-1,
            gamma=1e-4,
            background_color=torch.tensor([1.0, 1.0, 1.0], device=device),
        )
        renderer = MeshRenderer(
            rasterizer=MeshRasterizer(cameras=cameras, raster_settings=raster_settings),
            shader=HardPhongShader(
                device=device, blend_params=blend_params, cameras=cameras, lights=lights
            ),
        )

        output = renderer(mesh)

        image_ref = load_rgb_image("test_joinverts_final.png", DATA_DIR)

        if DEBUG:
            debugging_outputs = []
            for mesh_ in [mesh1, mesh2]:
                debugging_outputs.append(renderer(mesh_))
            Image.fromarray(
                (output[0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_joinverts_final_.png")
            Image.fromarray(
                (debugging_outputs[0][0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_joinverts_1.png")
            Image.fromarray(
                (debugging_outputs[1][0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_joinverts_2.png")

        result = output[0, ..., :3].cpu()
        self.assertClose(result, image_ref, atol=0.05)

    def test_join_atlas(self):
        """Meshes with TexturesAtlas joined into a scene"""
        # Test the result of rendering two tori with separate textures.
        # The expected result is consistent with rendering them each alone.
        torch.manual_seed(1)
        device = torch.device("cuda:0")
        plain_torus = torus(r=1, R=4, sides=5, rings=6, device=device)
        [verts] = plain_torus.verts_list()
        verts_shifted1 = verts.clone()
        verts_shifted1 *= 1.2
        verts_shifted1[:, 0] += 4
        verts_shifted1[:, 1] += 5
        verts[:, 0] -= 4
        verts[:, 1] -= 4

        [faces] = plain_torus.faces_list()
        map_size = 3
        # Two random atlases.
        # The averaging of the random numbers here is not consistent with the
        # meaning of the atlases, but makes each face a bit smoother than
        # if everything had a random color.
        atlas1 = torch.rand(size=(faces.shape[0], map_size, map_size, 3), device=device)
        atlas1[:, 1] = 0.5 * atlas1[:, 0] + 0.5 * atlas1[:, 2]
        atlas1[:, :, 1] = 0.5 * atlas1[:, :, 0] + 0.5 * atlas1[:, :, 2]
        atlas2 = torch.rand(size=(faces.shape[0], map_size, map_size, 3), device=device)
        atlas2[:, 1] = 0.5 * atlas2[:, 0] + 0.5 * atlas2[:, 2]
        atlas2[:, :, 1] = 0.5 * atlas2[:, :, 0] + 0.5 * atlas2[:, :, 2]

        textures1 = TexturesAtlas(atlas=[atlas1])
        textures2 = TexturesAtlas(atlas=[atlas2])
        mesh1 = Meshes(verts=[verts], faces=[faces], textures=textures1)
        mesh2 = Meshes(verts=[verts_shifted1], faces=[faces], textures=textures2)
        mesh_joined = join_meshes_as_scene([mesh1, mesh2])

        R, T = look_at_view_transform(18, 0, 0)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)

        raster_settings = RasterizationSettings(
            image_size=512, blur_radius=0.0, faces_per_pixel=1
        )

        lights = PointLights(
            device=device,
            ambient_color=((1.0, 1.0, 1.0),),
            diffuse_color=((0.0, 0.0, 0.0),),
            specular_color=((0.0, 0.0, 0.0),),
        )
        blend_params = BlendParams(
            sigma=1e-1,
            gamma=1e-4,
            background_color=torch.tensor([1.0, 1.0, 1.0], device=device),
        )
        renderer = MeshRenderer(
            rasterizer=MeshRasterizer(cameras=cameras, raster_settings=raster_settings),
            shader=HardPhongShader(
                device=device, blend_params=blend_params, cameras=cameras, lights=lights
            ),
        )

        output = renderer(mesh_joined)

        image_ref = load_rgb_image("test_joinatlas_final.png", DATA_DIR)

        if DEBUG:
            debugging_outputs = []
            for mesh_ in [mesh1, mesh2]:
                debugging_outputs.append(renderer(mesh_))
            Image.fromarray(
                (output[0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_joinatlas_final_.png")
            Image.fromarray(
                (debugging_outputs[0][0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_joinatlas_1.png")
            Image.fromarray(
                (debugging_outputs[1][0, ..., :3].cpu().numpy() * 255).astype(np.uint8)
            ).save(DATA_DIR / "test_joinatlas_2.png")

        result = output[0, ..., :3].cpu()
        self.assertClose(result, image_ref, atol=0.05)

    def test_joined_spheres(self):
        """
        Test a list of Meshes can be joined as a single mesh and
        the single mesh is rendered correctly with Phong, Gouraud
        and Flat Shaders.
        """
        device = torch.device("cuda:0")

        # Init mesh with vertex textures.
        # Initialize a list containing two ico spheres of different sizes.
        sphere_list = [ico_sphere(3, device), ico_sphere(4, device)]
        # [(42 verts, 80 faces), (162 verts, 320 faces)]
        # The scale the vertices need to be set at to resize the spheres
        scales = [0.25, 1]
        # The distance the spheres ought to be offset horizontally to prevent overlap.
        offsets = [1.2, -0.3]
        # Initialize a list containing the adjusted sphere meshes.
        sphere_mesh_list = []
        for i in range(len(sphere_list)):
            verts = sphere_list[i].verts_padded() * scales[i]
            verts[0, :, 0] += offsets[i]
            sphere_mesh_list.append(
                Meshes(verts=verts, faces=sphere_list[i].faces_padded())
            )
        joined_sphere_mesh = join_meshes_as_scene(sphere_mesh_list)
        joined_sphere_mesh.textures = TexturesVertex(
            verts_features=torch.ones_like(joined_sphere_mesh.verts_padded())
        )

        # Init rasterizer settings
        R, T = look_at_view_transform(2.7, 0.0, 0.0)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)
        raster_settings = RasterizationSettings(
            image_size=512, blur_radius=0.0, faces_per_pixel=1
        )

        # Init shader settings
        materials = Materials(device=device)
        lights = PointLights(device=device)
        lights.location = torch.tensor([0.0, 0.0, +2.0], device=device)[None]
        blend_params = BlendParams(1e-4, 1e-4, (0, 0, 0))

        # Init renderer
        rasterizer = MeshRasterizer(cameras=cameras, raster_settings=raster_settings)
        shaders = {
            "phong": HardPhongShader,
            "gouraud": HardGouraudShader,
            "flat": HardFlatShader,
        }
        for (name, shader_init) in shaders.items():
            shader = shader_init(
                lights=lights,
                cameras=cameras,
                materials=materials,
                blend_params=blend_params,
            )
            renderer = MeshRenderer(rasterizer=rasterizer, shader=shader)
            image = renderer(joined_sphere_mesh)
            rgb = image[..., :3].squeeze().cpu()
            if DEBUG:
                file_name = "DEBUG_joined_spheres_%s.png" % name
                Image.fromarray((rgb.numpy() * 255).astype(np.uint8)).save(
                    DATA_DIR / file_name
                )
            image_ref = load_rgb_image("test_joined_spheres_%s.png" % name, DATA_DIR)
            self.assertClose(rgb, image_ref, atol=0.05)

    def test_texture_map_atlas(self):
        """
        Test a mesh with a texture map as a per face atlas is loaded and rendered correctly.
        """
        device = torch.device("cuda:0")
        obj_dir = Path(__file__).resolve().parent.parent / "docs/tutorials/data"
        obj_filename = obj_dir / "cow_mesh/cow.obj"

        # Load mesh and texture as a per face texture atlas.
        verts, faces, aux = load_obj(
            obj_filename,
            device=device,
            load_textures=True,
            create_texture_atlas=True,
            texture_atlas_size=8,
            texture_wrap=None,
        )
        mesh = Meshes(
            verts=[verts],
            faces=[faces.verts_idx],
            textures=TexturesAtlas(atlas=[aux.texture_atlas]),
        )

        # Init rasterizer settings
        R, T = look_at_view_transform(2.7, 0, 0)
        cameras = FoVPerspectiveCameras(device=device, R=R, T=T)

        raster_settings = RasterizationSettings(
            image_size=512, blur_radius=0.0, faces_per_pixel=1, cull_backfaces=True
        )

        # Init shader settings
        materials = Materials(device=device, specular_color=((0, 0, 0),), shininess=0.0)
        lights = PointLights(device=device)

        # Place light behind the cow in world space. The front of
        # the cow is facing the -z direction.
        lights.location = torch.tensor([0.0, 0.0, 2.0], device=device)[None]

        # The HardPhongShader can be used directly with atlas textures.
        renderer = MeshRenderer(
            rasterizer=MeshRasterizer(cameras=cameras, raster_settings=raster_settings),
            shader=HardPhongShader(lights=lights, cameras=cameras, materials=materials),
        )

        images = renderer(mesh)
        rgb = images[0, ..., :3].squeeze().cpu()

        # Load reference image
        image_ref = load_rgb_image("test_texture_atlas_8x8_back.png", DATA_DIR)

        if DEBUG:
            Image.fromarray((rgb.numpy() * 255).astype(np.uint8)).save(
                DATA_DIR / "DEBUG_texture_atlas_8x8_back.png"
            )

        self.assertClose(rgb, image_ref, atol=0.05)
