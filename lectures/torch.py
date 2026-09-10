"""Minimal edtrace compatibility shim for lectures without tensor code.

The upstream executor imports torch only to recognize tensor values. The
DeepEval lectures do not create tensors, so downloading a CUDA PyTorch build
would add a large unrelated dependency.
"""


class Tensor:  # noqa: D101
    pass
