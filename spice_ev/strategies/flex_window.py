import warnings
from spice_ev.strategies.peak_load_window import PeakLoadWindow


class FlexWindow(PeakLoadWindow):
    """ Charging during given time windows. """
    def __init__(self, components, start_time, **kwargs):
        warnings.warn("flex_window is deprecated, use peak_load_window instead")
        # flex_windows does not work with time_windows
        kwargs.pop("time_windows", None)
        super().__init__(components, start_time, **kwargs)
