Core API Reference
==================

Status objects
--------------

Ophyd Status objects signal when some potentially-lengthy action is complete.
The action may be moving a motor, acquiring an image, or waiting for a
temperature controller to reach a setpoint. From a general software engineering
point of view, they are like :obj:`concurrent.futures.Future` objects in the
Python standard library but with some semantics specific to controlling
physical hardware.

The lifecycle of a Status object is:

#. A Status object is created with an associated timeout. The timeout clock
   starts.
#. The recipient of the Status object may add callbacks that will be notified
   when the Status object completes.
#. The Status object is marked as completed successfully, or marked as
   completed with an error, or the timeout is reached, whichever happens first.
   The callbacks are called in any case.

Creation and Marking Completion
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A *timeout*, given in seconds, is optional but strongly recommended. (The
default, ``None`` means it will wait forever to be marked completed.)

.. code:: python

   from ophyd import Status

   status = Status(timeout=60)

Additionally, it accepts a *settle_time*, an extra delay which will be added
between the control system reporting successful completion and the Status being
marked as finished. This is also given in seconds. It is ``0`` by default.

.. code:: python

   status = Status(timeout=60, settle_time=10)

The status should be notified by the control system, typically from another
thread or task, when some action is complete. To mark success, call
:obj:`StatusBase.set_finished`. To mark failure, call
:obj:`StatusBase.set_exception`, passing it an Exception giving information
about the cause of failure.

As a toy example, we could hook it up to a :obj:`threading.Timer` that marks it
as succeeded or failed based on a coin flip.

.. code:: python

   import random
   import threading

   def mark_done():
       if random.random() > 0.5:  # coin flip
           status.set_finished()  # success
       else:
           error = Exception("Bad luck")
           status.set_exception(error)  # failure

   # Run mark_done 5 seconds from now in a thread.
   threading.Timer(5, mark_done).start()

See the tutorials for more realistic examples involving integration with an
actual control system.

.. versionchanged:: v1.5.0

   In previous versions of ophyd, the Status objects were marked as completed
   by calling ``status._finished(success=True)`` or
   ``status._finished(success=False)``. This is still supported but the new
   methods ``status.set_finished()`` and ``status.set_exception(...)`` are
   recommended because they can provide more information about the *cause* of
   failure, and they match the Python standard library's
   :obj:`concurrent.futures.Future` interface.

Notification of Completion
^^^^^^^^^^^^^^^^^^^^^^^^^^

The recipient of the Status object can request synchronous or asynchronous
notification of completion. To wait synchronously, the :obj:`StatusBase.wait`
will block until the Status is marked as complete or a timeout has expired.

.. code:: python

   status.wait()  # Wait forever for the Status to finish or time out.
   status.wait(10)  # Wait for at most 10 seconds.

If and when the Status completes successfully, this will return ``None``. If
the Status is marked as failed, the exception (e.g. ``Exception("Bad luck")``
in our example above) will be raised. If the Status' own timeout has expired,
:obj:`~ophyd.StatusTimeoutError` will be raised. If a timeout given to
:obj:`StatusBase.wait` expires before any of these things happen,
:obj:`~ophyd.WaitTimeoutError` will be raised.

The method :obj:`StatusBase.exception` behaves similarly to :obj:`StatusBase.wait`; the
only difference is that if the Status is marked as failed or the Status' own
timeout expires it *returns* the exception rather than *raising* it. Both
return ``None`` if the Status finishes successfully, and both raise
:obj:`~ophyd.WaitTimeoutError` if the given timeout expires before the Status
completes or times out.

Alternatively, the recipient of the Status object can ask to be notified of
completion asynchronously by adding a callback. The callback will be called
when the Status is marked as complete or its timeout has expired. (If no
timeout was given, the callback might never be called. This is why providing a
timeout is strongly recommended.)

.. code:: python

   def callback(status):
       print(f"{status} is done")

   status.add_callback(callback)

Callbacks may be added at any time. Until the Status completes, it holds a hard
reference to each callback in a list, ``status.callbacks``. The list is cleared
when the callback completes. Any callbacks added to a Status object *after*
completion will be called immediately, and no reference will be held.

Each callback is passed the Status object as an argument, and it can use this
to distinguish success from failure.

.. code:: python

   def callback(status):
       error = status.exception()
       if error is None:
           print(f"{status} has completed successfully.")
       else:
           print(f"{status} has failed with error {error}.")

.. autoclass:: ophyd.StatusBase
   :members:

Device
------

.. autoclass:: ophyd.Device
   :members:

Signals
-------

.. autoclass:: ophyd.Signal
   :members:

.. autoclass:: ophyd.EpicsSignal
   :members:

.. autoclass:: ophyd.EpicsSignalRO
   :members:

Components
----------

.. autoclass:: ophyd.Component
   :members:

.. autoclass:: ophyd.FormattedComponent
   :members:
