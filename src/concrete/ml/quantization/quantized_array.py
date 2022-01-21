"""Quantization utilities for a numpy array/tensor."""
from copy import deepcopy
from typing import Optional, Tuple

import numpy

from ..common.debugging import assert_true


class QuantizedArray:
    """Abstraction of quantized array.

    See https://arxiv.org/abs/1712.05877.

    Args:
        values (numpy.ndarray): Values to be quantized.
        n_bits (int): The number of bits to use for quantization.
        is_signed (bool): Whether the quantization can be on signed integers.
        value_is_float (bool, optional): Whether the passed values are real (float) values or not.
            If False, the values will be quantized according to the passed scale and zero_point.
            Defaults to True.
        scale (Optional[float], optional): Ignored if value_is_float is True, otherwise needs to be
            provided.
            Defaults to None.
        zero_point (Optional[int], optional): Ignored if value_is_float is True, otherwise needs to
            be provided.
            Defaults to None.
    """

    STABILITY_CONST = 10 ** -6

    def __init__(
        self,
        n_bits: int,
        values: numpy.ndarray,
        is_signed: bool = False,
        value_is_float: bool = True,
        scale: Optional[float] = None,
        zero_point: Optional[int] = None,
    ):
        self.offset = 0
        if is_signed:
            self.offset = 2 ** (n_bits - 1)
        self.n_bits = n_bits
        self.is_signed = is_signed
        if value_is_float:
            self.values = deepcopy(values)
            self.scale, self.zero_point, self.qvalues = self.compute_quantization_parameters()
        else:
            assert_true(
                scale is not None and zero_point is not None,
                f"When initializing {self.__class__.__name__} with values_need_to_be_quantized == "
                "False, the scale and zero_point parameters are required.",
            )

            # For mypy
            assert scale is not None
            assert zero_point is not None

            self.scale = scale
            self.zero_point = zero_point
            self.qvalues = deepcopy(values)
            # Populate self.values
            self.dequant()

    def __call__(self) -> Optional[numpy.ndarray]:
        return self.qvalues

    def compute_quantization_parameters(self) -> Tuple[float, int, numpy.ndarray]:
        """Compute the quantization parameters.

        Returns:
            scale (float): The quantization scale.
            zero_point (int): The quantization zero point.
            qvalues (numpy.ndarray): The quantized values.
        """
        # Small constant needed for stability
        rmax = numpy.max(self.values)
        rmin = numpy.min(self.values)

        if rmax - rmin < self.STABILITY_CONST:
            # In this case there is  a single unique value to quantize

            # is is_signed is True, we need to set the offset back to 0.
            # Signed quantization does not make sense for a single value.
            self.offset = 0

            # This value could be multiplied with inputs at some point in the model
            # Since zero points need to be integers, if this value is a small float (ex: 0.01)
            # it will be quantized to 0 with a 0 zero-point, thus becoming useless in multiplication

            if numpy.abs(rmax) < self.STABILITY_CONST:
                # If the value is a 0 we cannot do it since the scale would become 0 as well
                # resulting in division by 0
                scale = 1
                # Ideally we should get rid of round here but it is risky
                # regarding the FHE compilation.
                # Indeed, the zero_point value for the weights has to be an integer
                # for the compilation to work.
                zero_point = numpy.round(-rmin)
            else:
                # If the value is not a 0 we can tweak the scale factor so that
                # the value quantizes to 2^b - 1, the highest possible quantized value

                # TODO: should we quantize it to the value of 1 what ever the number of bits
                # in order to save some precision bits ?
                scale = rmax / (2 ** self.n_bits - 1)
                zero_point = 0
        else:
            scale = (rmax - rmin) / (2 ** self.n_bits - 1) if rmax != rmin else 1.0

            zero_point = numpy.round(
                (rmax * (-self.offset) - (rmin * (2 ** self.n_bits - 1 - self.offset)))
                / (rmax - rmin)
            ).astype(int)

        # Compute quantized values and store
        qvalues = self.values / scale + zero_point

        qvalues = (
            numpy.rint(qvalues)
            .clip(-self.offset, 2 ** (self.n_bits) - 1 - self.offset)
            .astype(int)  # Careful this can be very large with high number of bits
        )

        return scale, zero_point, qvalues

    def update_values(self, values: numpy.ndarray) -> Optional[numpy.ndarray]:
        """Update values to get their corresponding qvalues using the related quantized parameters.

        Args:
            values (numpy.ndarray): Values to replace self.values

        Returns:
            qvalues (numpy.ndarray): Corresponding qvalues
        """
        self.values = deepcopy(values)
        self.quant()
        return self.qvalues

    def update_qvalues(self, qvalues: numpy.ndarray) -> Optional[numpy.ndarray]:
        """Update qvalues to get their corresponding values using the related quantized parameters.

        Args:
            qvalues (numpy.ndarray): Values to replace self.qvalues

        Returns:
            values (numpy.ndarray): Corresponding values
        """
        self.qvalues = deepcopy(qvalues)
        self.dequant()
        return self.values

    def quant(self) -> Optional[numpy.ndarray]:
        """Quantize self.values.

        Returns:
            numpy.ndarray: Quantized values.
        """

        self.qvalues = (
            numpy.rint(self.values / self.scale + self.zero_point)
            .clip(-self.offset, 2 ** (self.n_bits) - 1 - self.offset)
            .astype(int)
        )
        return self.qvalues

    def dequant(self) -> numpy.ndarray:
        """Dequantize self.qvalues.

        Returns:
            numpy.ndarray: Dequantized values.
        """
        # TODO: https://github.com/zama-ai/concrete-numpy-internal/issues/721
        # remove this + (-x) when the above issue is done
        self.values = self.scale * (self.qvalues + -(self.zero_point))
        return self.values
