# Copyright (c) Facebook, Inc. and its affiliates. All rights reserved.


from typing import NamedTuple, Sequence

import torch

# pyre-fixme[21]: Could not find name `_C` in `pytorch3d`.
from pytorch3d import _C


# Example functions for blending the top K colors per pixel using the outputs
# from rasterization.
# NOTE: All blending function should return an RGBA image per batch element


# Data class to store blending params with defaults
class BlendParams(NamedTuple):
    sigma: float = 1e-4
    gamma: float = 1e-4
    background_color: Sequence = (1.0, 1.0, 1.0)


def hard_rgb_blend(colors, fragments, blend_params) -> torch.Tensor:
    """
    Naive blending of top K faces to return an RGBA image
      - **RGB** - choose color of the closest point i.e. K=0
      - **A** - 1.0

    Args:
        colors: (N, H, W, K, 3) RGB color for each of the top K faces per pixel.
        fragments: the outputs of rasterization. From this we use
            - pix_to_face: LongTensor of shape (N, H, W, K) specifying the indices
              of the faces (in the packed representation) which
              overlap each pixel in the image. This is used to
              determine the output shape.
        blend_params: BlendParams instance that contains a background_color
        field specifying the color for the background
    Returns:
        RGBA pixel_colors: (N, H, W, 4)
    """
    N, H, W, K = fragments.pix_to_face.shape
    device = fragments.pix_to_face.device

    # Mask for the background.
    is_background = fragments.pix_to_face[..., 0] < 0  # (N, H, W)

    if torch.is_tensor(blend_params.background_color):
        background_color = blend_params.background_color.to(device)
    else:
        background_color = colors.new_tensor(blend_params.background_color)  # (3)

    # Find out how much background_color needs to be expanded to be used for masked_scatter.
    num_background_pixels = is_background.sum()

    # Set background color.
    pixel_colors = colors[..., 0, :].masked_scatter(
        is_background[..., None],
        background_color[None, :].expand(num_background_pixels, -1),
    )  # (N, H, W, 3)

    # Concat with the alpha channel.
    alpha = torch.ones((N, H, W, 1), dtype=colors.dtype, device=device)
    return torch.cat([pixel_colors, alpha], dim=-1)  # (N, H, W, 4)


# Wrapper for the C++/CUDA Implementation of sigmoid alpha blend.
class _SigmoidAlphaBlend(torch.autograd.Function):
    @staticmethod
    def forward(ctx, dists, pix_to_face, sigma):
        alphas = _C.sigmoid_alpha_blend(dists, pix_to_face, sigma)
        ctx.save_for_backward(dists, pix_to_face, alphas)
        ctx.sigma = sigma
        return alphas

    @staticmethod
    def backward(ctx, grad_alphas):
        dists, pix_to_face, alphas = ctx.saved_tensors
        sigma = ctx.sigma
        grad_dists = _C.sigmoid_alpha_blend_backward(
            grad_alphas, alphas, dists, pix_to_face, sigma
        )
        return grad_dists, None, None


# pyre-fixme[16]: `_SigmoidAlphaBlend` has no attribute `apply`.
_sigmoid_alpha = _SigmoidAlphaBlend.apply


def sigmoid_alpha_blend(colors, fragments, blend_params) -> torch.Tensor:
    """
    Silhouette blending to return an RGBA image
      - **RGB** - choose color of the closest point.
      - **A** - blend based on the 2D distance based probability map [1].

    Args:
        colors: (N, H, W, K, 3) RGB color for each of the top K faces per pixel.
        fragments: the outputs of rasterization. From this we use
            - pix_to_face: LongTensor of shape (N, H, W, K) specifying the indices
              of the faces (in the packed representation) which
              overlap each pixel in the image.
            - dists: FloatTensor of shape (N, H, W, K) specifying
              the 2D euclidean distance from the center of each pixel
              to each of the top K overlapping faces.

    Returns:
        RGBA pixel_colors: (N, H, W, 4)

    [1] Liu et al, 'Soft Rasterizer: A Differentiable Renderer for Image-based
        3D Reasoning', ICCV 2019
    """
    N, H, W, K = fragments.pix_to_face.shape
    pixel_colors = torch.ones((N, H, W, 4), dtype=colors.dtype, device=colors.device)
    pixel_colors[..., :3] = colors[..., 0, :]
    alpha = _sigmoid_alpha(fragments.dists, fragments.pix_to_face, blend_params.sigma)
    pixel_colors[..., 3] = alpha
    return pixel_colors


def softmax_rgb_blend(
    colors, fragments, blend_params, znear: float = 1.0, zfar: float = 100
) -> torch.Tensor:
    """
    RGB and alpha channel blending to return an RGBA image based on the method
    proposed in [1]
      - **RGB** - blend the colors based on the 2D distance based probability map and
        relative z distances.
      - **A** - blend based on the 2D distance based probability map.

    Args:
        colors: (N, H, W, K, 3) RGB color for each of the top K faces per pixel.
        fragments: namedtuple with outputs of rasterization. We use properties
            - pix_to_face: LongTensor of shape (N, H, W, K) specifying the indices
              of the faces (in the packed representation) which
              overlap each pixel in the image.
            - dists: FloatTensor of shape (N, H, W, K) specifying
              the 2D euclidean distance from the center of each pixel
              to each of the top K overlapping faces.
            - zbuf: FloatTensor of shape (N, H, W, K) specifying
              the interpolated depth from each pixel to to each of the
              top K overlapping faces.
        blend_params: instance of BlendParams dataclass containing properties
            - sigma: float, parameter which controls the width of the sigmoid
              function used to calculate the 2D distance based probability.
              Sigma controls the sharpness of the edges of the shape.
            - gamma: float, parameter which controls the scaling of the
              exponential function used to control the opacity of the color.
            - background_color: (3) element list/tuple/torch.Tensor specifying
              the RGB values for the background color.
        znear: float, near clipping plane in the z direction
        zfar: float, far clipping plane in the z direction

    Returns:
        RGBA pixel_colors: (N, H, W, 4)

    [0] Shichen Liu et al, 'Soft Rasterizer: A Differentiable Renderer for
    Image-based 3D Reasoning'
    """

    N, H, W, K = fragments.pix_to_face.shape
    device = fragments.pix_to_face.device
    pixel_colors = torch.ones((N, H, W, 4), dtype=colors.dtype, device=colors.device)
    background = blend_params.background_color
    if not torch.is_tensor(background):
        background = torch.tensor(background, dtype=torch.float32, device=device)
    else:
        background = background.to(device)

    # Weight for background color
    eps = 1e-10

    # Mask for padded pixels.
    mask = fragments.pix_to_face >= 0

    # Sigmoid probability map based on the distance of the pixel to the face.
    prob_map = torch.sigmoid(-fragments.dists / blend_params.sigma) * mask

    # The cumulative product ensures that alpha will be 0.0 if at least 1
    # face fully covers the pixel as for that face, prob will be 1.0.
    # This results in a multiplication by 0.0 because of the (1.0 - prob)
    # term. Therefore 1.0 - alpha will be 1.0.
    alpha = torch.prod((1.0 - prob_map), dim=-1)

    # Weights for each face. Adjust the exponential by the max z to prevent
    # overflow. zbuf shape (N, H, W, K), find max over K.
    # TODO: there may still be some instability in the exponent calculation.

    z_inv = (zfar - fragments.zbuf) / (zfar - znear) * mask
    # pyre-fixme[16]: `Tuple` has no attribute `values`.
    # pyre-fixme[6]: Expected `Tensor` for 1st param but got `float`.
    z_inv_max = torch.max(z_inv, dim=-1).values[..., None].clamp(min=eps)
    # pyre-fixme[6]: Expected `Tensor` for 1st param but got `float`.
    weights_num = prob_map * torch.exp((z_inv - z_inv_max) / blend_params.gamma)

    # Also apply exp normalize trick for the background color weight.
    # Clamp to ensure delta is never 0.
    # pyre-fixme[20]: Argument `max` expected.
    # pyre-fixme[6]: Expected `Tensor` for 1st param but got `float`.
    delta = torch.exp((eps - z_inv_max) / blend_params.gamma).clamp(min=eps)

    # Normalize weights.
    # weights_num shape: (N, H, W, K). Sum over K and divide through by the sum.
    denom = weights_num.sum(dim=-1)[..., None] + delta

    # Sum: weights * textures + background color
    weighted_colors = (weights_num[..., None] * colors).sum(dim=-2)
    weighted_background = delta * background
    pixel_colors[..., :3] = (weighted_colors + weighted_background) / denom
    pixel_colors[..., 3] = 1.0 - alpha

    return pixel_colors
