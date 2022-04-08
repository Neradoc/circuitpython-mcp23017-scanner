"""
MCP23017, I2C GPIO expander
connected to a Neokey5x6 (matrix keypad)
"""
from digitalio import DigitalInOut, Pull
from supervisor import ticks_ms

class Event:
    """
    A key transition event.
    
    :param int key_number: the key number
    :param bool pressed: ``True`` if the key was pressed; ``False`` if it was released.
    :param int timestamp: The time in milliseconds that the keypress occurred in the
                          `supervisor.ticks_ms` time system.  If specified as None,
                          the current value of `supervisor.ticks_ms` is used.

    
    """
    def __init__(self, key, pressed, timestamp=None):
        self.key_number = key
        """The key number."""
        self.timestamp = timestamp or ticks_ms()
        """The timestamp."""
        self.pressed = pressed
        """True if the event represents a key down (pressed) transition.
        The opposite of released."""

    @property
    def released(self):
        """True if the event represents a key up (released) transition.
        The opposite of pressed."""
        return not self.pressed

    def __eq__(self, other):
        """Two Event objects are equal if their key_number and pressed/released values
        are equal. Note that this does not compare the event timestamps."""
        return (
            self.key_number == other.key_number
            and self.pressed == other.pressed
        )

    def __hash__(self):
        """Returns a hash for the Event, so it can be used in dictionaries, etc..
        Note that as events with different timestamps compare equal,
        they also hash to the same value."""
        return (
            self.key_number << 1
            + int(self.pressed)
        )


class EventQueue:
    def __init__(self): # , max_events=64):
        self._outq = []
        self._inq = []

    def append(self, event):
        """Append an event at the end of the queue"""
        self._inq.append(event)

    def get(self):
        """
        Return the next key transition event.
        Return None if no events are pending.
        """
        if self._outq:
            return self._outq.pop()
        if len(self._inq) == 1:
            return self._inq.pop()
        if self._inq:
            self._outq = list(reversed(self._inq))
            self._inq.clear()
            return self._outq.pop()
        return None

    def get_into(self, event):
        """
        Store the next key transition event in the supplied event, if available,
        and return True. If there are no queued events, do not touch event
        and return False.
        Note: in python this does not optimize to avoid allocating.
        """
        ev = self.get()
        if ev:
            event.key_number = ev.key_number
            event.timestamp  = ev.timestamp
            event.pressed    = ev.pressed
            return True
        return False

    def clear(self):
        """Clear any queued key transition events."""
        self._outq.clear()
        self._inq.clear()

    def __bool__(self):
        """
        True if len() is greater than zero.
        This is an easy way to check if the queue is empty.
        """
        return len(self) > 0

    def __len__(self):
        """
        Return the number of events currently in the queue.
        Used to implement len().
        """
        return len(self._outq) + len(self._inq)


class McpMatrixScanner:
    """
    Columns are on port A and inputs.
    Rows are on port B and outputs.
    """
    def __init__(self, mcp, row_pins, column_pins, irq=None):
        self._key_count = len(column_pins) * len(row_pins)
        self.columns = column_pins
        self.rows = row_pins
        self.mcp = mcp
        self.keys_state = set()
        self.events = EventQueue()
        # set port A to output (columns)
        mcp.iodira = 0x00
        # set port B to input (rows) all pull ups
        mcp.iodirb = 0xFF
        mcp.gppub = 0xFF
        # set interrupts
        self.irq = None
        if irq:
            self.irq = DigitalInOut(irq)
            self.irq.switch_to_input(Pull.UP)
            # TODO: configure mcp based on row and column numbers
            #       to leave the other pins free to use ?
            mcp.interrupt_enable = 0xFF00
            mcp.default_value = 0xFFFF
            # compare input to default value (1) or previous value (0)
            mcp.interrupt_configuration = 0xFF00
            mcp.io_control = 0x44  # Interrupt as open drain and mirrored
            mcp.clear_ints()

    @property
    def key_count(self):
        return self._key_count

    def _scan_matrix(self):
        """Scan the matrix and return the list of keys down"""
        pressed = set()
        num_cols = len(self.columns)
        for scan_column in self.columns:
            # set all outputs to 1 on port A except the scan_column
            self.mcp.gpioa = 0xFF - (1 << scan_column)
            if self.irq is None or not self.irq.value:
                # read the input
                inputs = self.mcp.gpiob
                if inputs:
                    # adds (columns,row) if the row is 0 too
                    for row in self.rows:
                        if (inputs >> row) & 1 == 0:
                            pressed.add(scan_column + num_cols * row)
        # set back port A to default
        self.mcp.gpioa = 0xFF
        return pressed

    def update_queue(self):
        """
        Run the scan and create events in the event queue.
        """
        timestamp = ticks_ms()
        # scan the matrix, find Neo
        current_state = self._scan_matrix()
        # use set algebra to find released and pressed keys
        released_keys = self.keys_state - current_state
        pressed_keys = current_state - self.keys_state
        # create the events into the queue
        for key in released_keys:
            self.events.append(Event(key, False, timestamp))
        for key in pressed_keys:
            self.events.append(Event(key, True, timestamp))
        # end
        self.keys_state = current_state

    def key_number_to_row_column(self, key_number: int) -> Tuple[int]:
        """Convert key number to row, column"""
        row = key_number // len(self.columns)
        column = key_number % len(self.columns)
        return (row, column)

    def row_column_to_key_number(self, row: int, column: int) -> int:
        """Convert row, column to key number"""
        return row * len(self.columns) + column

    def reset(self):
        """
        Reset the internal state of the scanner to assume that all keys are now
        released. Any key that is already pressed at the time of this call will
        therefore cause a new key-pressed event to occur on the next scan.
        """
        self.events.clear()
        self.keys_state.clear()

    def deinit(self):
        """Release the IRQ pin"""
        if self.irq:
            self.irq.deinit()
            self.irq = None
        # TODO: reset the mcp configuration

    def __enter__(self):
        """No-op used by Context Managers."""
        return self

    def __exit__(self):
        """Automatically deinitializes when exiting a context."""
        self.deinit()
