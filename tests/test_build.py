# Copyright (c) Facebook, Inc. and its affiliates. All rights reserved.
import os
import unittest
from collections import Counter
from pathlib import Path


# This file groups together tests which look at the code without running it.
# When running the tests inside conda's build, the code is not available.
in_conda_build = os.environ.get("CONDA_BUILD_STATE", "") == "TEST"


class TestBuild(unittest.TestCase):
    @unittest.skipIf(in_conda_build, "In conda build")
    def test_name_clash(self):
        # For setup.py, all translation units need distinct names, so we
        # cannot have foo.cu and foo.cpp, even in different directories.
        test_dir = Path(__file__).resolve().parent
        source_dir = test_dir.parent / "pytorch3d"

        stems = []
        for extension in [".cu", ".cpp"]:
            files = source_dir.glob(f"**/*{extension}")
            stems.extend(f.stem for f in files)

        counter = Counter(stems)
        for k, v in counter.items():
            self.assertEqual(v, 1, f"Too many files with stem {k}.")

    @unittest.skipIf(in_conda_build, "In conda build")
    def test_copyright(self):
        test_dir = Path(__file__).resolve().parent
        root_dir = test_dir.parent

        extensions = ("py", "cu", "cuh", "cpp", "h", "hpp", "sh")

        expect = (
            "Copyright (c) Facebook, Inc. and its affiliates."
            + " All rights reserved.\n"
        )

        for extension in extensions:
            for i in root_dir.glob(f"**/*.{extension}"):
                if str(i).endswith(
                    "pytorch3d/transforms/external/kornia_angle_axis_to_rotation_matrix.py"
                ):
                    continue
                if str(i).endswith("pytorch3d/csrc/pulsar/include/fastermath.h"):
                    continue
                with open(i) as f:
                    firstline = f.readline()
                    if firstline.startswith(("# -*-", "#!")):
                        firstline = f.readline()
                    self.assertTrue(
                        firstline.endswith(expect), f"{i} missing copyright header."
                    )
