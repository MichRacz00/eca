import queue
import util
import collections
import threading
import sys

from contextlib import contextmanager

# all exported names
__all__ = [
    'event',
    'condition',
    'rules',
    'Context',
    'Event',
    'new_event',
    'context_switch'
]

# The 'global' rules set
rules = set()

# The thread local storage (used for 'current context' queries)
thread_local = threading.local()


class Event:
    """Abstract event with a name and attributes."""
    def __init__(self, name, data=None):
        """Constructs an event.

        Attributes are optional.

        """
        self.name = name
        self.data = data or {}

        assert isinstance(self.data, collections.Mapping)

    def __getattr__(self, name):
        return self.data[name]

    def __str__(self):
        data_strings = []
        for k, v in self.data.items():
            data_strings.append("{}={}".format(k, v))
        return "'{}' with {{{}}}".format(self.name, ', '.join(data_strings))


class Context:
    """ECA Execution context.

    Each context maintains both a variables namespace and an event queue. The
    context itself provides a run method to allow threaded execution.

    """
    def __init__(self, trace=False):
        self.event_queue = queue.Queue()
        self.scope = util.NamespaceDict()
        self.done = False
        self.trace = trace

    def _trace(self, message):
        """Prints tracing statements if trace is enabled."""
        if self.trace:
            print("({})".format(message), file=sys.stderr)

    def receive_event(self, event):
        """Receives an Event to handle."""
        self._trace("Received event: {}".format(event))
        self.event_queue.put(event)

    def start(self):
        """Starts the parallel processing of this context."""
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def run(self):
        """Main event loop."""
        # set context for the current thread (regardless of which one it is)
        with context_switch(self):
            while not self.done:
                self._handle_one()

    def _handle_one(self):
        """Handles a single event, or times out after receiving nothing."""
        try:
            # wait until we have an upcoming event
            # (but don't wait too long -- self.done could have been set to
            #  true while we were waiting for an event)
            event = self.event_queue.get(timeout=1.0)

            self._trace("Working on event: {}".format(event))

            # Determine candidate rules and execute matches:
            # 1) Only rules that match the event name as one of the events
            candidates = [r for r in rules if event.name in r.events]

            # 2) Only rules for which all conditions hold
            for r in candidates:
                if not [c(self.scope, event) for c in r.conditions].count(False):
                    self._trace("Rule: {}".format(util.describe_function(r)))
                    result = r(self.scope, event)

        except queue.Empty:
            # Timeout on waiting, loop to check condition
            pass


@contextmanager
def context_switch(context):
    """Context manager to allow ad-hoc context switches.

    This function can be written without any regard for locking as the
    thread_local object will take care of that. Since everything here is done
    in the same thread, this effectively allows nesting of context switches.

    """
    # stash old context
    old_context = getattr(thread_local, 'context', None)

    # switch to new context
    thread_local.context = context

    # yield control to block inside 'with'
    yield

    # restore old context
    thread_local.context = old_context


def new_event(eventname, data=None):
    """Emits a new event.

    This function emits a new event to react on.

    """
    e = Event(eventname, data)
    if getattr(thread_local, 'context', None) is None:
        raise NotImplementedError("Can't invoke new_event without a current context.")
    thread_local.context.receive_event(e)


def prepare_action(fn):
    """Prepares a function to be usable as an action.

    This function assigns an empty list of the 'conditions' attribute if it is
    not yet available. This function also registers the action with the action
    library.

    """
    fn.conditions = getattr(fn, 'conditions', [])
    fn.events = getattr(fn, 'events', set())
    rules.add(fn)


def condition(c):
    """Adds a condition callable to the action.

    The condition must be callable. The condition will receive a context and
    an event, and must return True or False.

    This function returns a decorator so we can pass an argument to the
    decorator itself. This is why we define a new function and return it
    without calling it.

    (See http://docs.python.org/3/glossary.html#term-decorator)

    """
    def condition_decorator(fn):
        prepare_action(fn)
        fn.conditions.append(c)
        return fn
    return condition_decorator


def event(eventname):
    """Attaches the action to an event.

    This is effectively the same as adding the 'event.name == eventname'
    condition. Adding multiple event names will prevent the rule from
    triggering.

    As condition, this function generates a decorator.

    """
    def event_decorator(fn):
        prepare_action(fn)
        fn.events.add(eventname)
        return fn
    return event_decorator
