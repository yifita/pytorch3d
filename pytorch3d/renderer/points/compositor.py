# Copyright (c) Facebook, Inc. and its affiliates. All rights reserved.

import warnings
from typing import List, Optional, Tuple, Union

import torch
import torch.nn as nn

from ..compositing import alpha_composite, norm_weighted_sum


# A compositor should take as input 3D points and some corresponding information.
# Given this information, the compositor can:
#     - blend colors across the top K vertices at a pixel


class AlphaCompositor(nn.Module):
    """
    Accumulate points using alpha compositing.
    """

    def __init__(
        self, background_color: Optional[Union[Tuple, List, torch.Tensor]] = None
    ):
        super().__init__()
        self.background_color = background_color

    def forward(self, fragments, alphas, ptclds, **kwargs) -> torch.Tensor:
        background_color = kwargs.get("background_color", self.background_color)
        images = alpha_composite(fragments, alphas, ptclds)

        # images are of shape (N, C, H, W)
        # check for background color & feature size C (C=4 indicates rgba)
        if background_color is not None and images.shape[1] == 4:
            return _add_background_color_to_images(fragments, images, background_color)
        return images


class NormWeightedCompositor(nn.Module):
    """
    Accumulate points using a normalized weighted sum.
    """

    def __init__(
        self, background_color: Optional[Union[Tuple, List, torch.Tensor]] = None
    ):
        super().__init__()
        self.background_color = background_color

    def forward(self, fragments, alphas, ptclds, **kwargs) -> torch.Tensor:
        background_color = kwargs.get("background_color", self.background_color)
        images = norm_weighted_sum(fragments, alphas, ptclds)

        # images are of shape (N, C, H, W)
        # check for background color & feature size C (C=4 indicates rgba)
        if background_color is not None and images.shape[1] == 4:
            return _add_background_color_to_images(fragments, images, background_color)
        return images


def _add_background_color_to_images(pix_idxs, images, background_color):
    """
    Mask pixels in images without corresponding points with a given background_color.

    Args:
        pix_idxs: int32 Tensor of shape (N, points_per_pixel, image_size, image_size)
            giving the indices of the nearest points at each pixel, sorted in z-order.
        images: Tensor of shape (N, 4, image_size, image_size) giving the
            accumulated features at each point, where 4 refers to a rgba feature.
        background_color: Tensor, list, or tuple with 3 or 4 values indicating the rgb/rgba
            value for the new background. Values should be in the interval [0,1].
     Returns:
        images: Tensor of shape (N, 4, image_size, image_size), where pixels with
            no nearest points have features set to the background color, and other
            pixels with accumulated features have unchanged values.
    """
    # Initialize background mask
    background_mask = pix_idxs[:, 0] < 0  # (N, image_size, image_size)

    # Convert background_color to an appropriate tensor and check shape
    if not torch.is_tensor(background_color):
        background_color = images.new_tensor(background_color)

    background_shape = background_color.shape

    if len(background_shape) != 1 or background_shape[0] not in (3, 4):
        warnings.warn(
            "Background color should be size (3) or (4), but is size %s instead"
            % (background_shape,)
        )
        return images

    background_color = background_color.to(images)

    # add alpha channel
    if background_shape[0] == 3:
        alpha = images.new_ones(1)
        background_color = torch.cat([background_color, alpha])

    num_background_pixels = background_mask.sum()

    # permute so that features are the last dimension for masked_scatter to work
    masked_images = images.permute(0, 2, 3, 1)[..., :4].masked_scatter(
        background_mask[..., None],
        background_color[None, :].expand(num_background_pixels, -1),
    )

    return masked_images.permute(0, 3, 1, 2)
