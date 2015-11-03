import functools
import inspect
from weakref import WeakKeyDictionary
from inspect import Parameter, Signature
import sys
import re
import epics
from ..signal import (Signal as osig, EpicsSignal as oesig,
                      SignalGroup as osiggrp)
from ...utils.epics_pvs import raise_if_disconnected
from ...utils.errors import DisconnectedError
from . import docs


def raise_if_disconnected(fcn):
    '''Decorator to catch attempted access to disconnected EPICS channels.'''
    # differs from implementation in utils.py because it gives pvname, not
    # object name
    @functools.wraps(fcn)
    def wrapper(self, *args, **kwargs):
        if self.connected:
            return fcn(self, *args, **kwargs)
        else:
            raise DisconnectedError('{} is not connected'.format(
                self.pv.pvname))
    return wrapper


class EpicsSignalLite:
    """A simplified EpicsSignal for demo purposes only
    
    This is a wrapper around a single, read-only epics.PV.
    """
    def __init__(self, pv_name, *args, **kwargs):
        self.pv = epics.PV(pv_name, *args, **kwargs)

    @property
    def connected(self):
        return self.pv.connected

    @raise_if_disconnected
    def get(self, *args, **kwargs):
        # pass through
        return self.pv.get(*args, **kwargs)


class EpicsSignalLiteRW(EpicsSignalLite):
    """A simplified EpicsSignal for demo purposes only
    
    This is a wrapper around a writeable PV and its readback value.
    """
    def __init__(self, pv_name, *args, **kwargs):
        super().__init__(pv_name, *args, **kwargs)
        self.readback_pv = epics.PV('{}.RBV'.format(pv_name))

    @property
    def connected(self):
        return self.pv.connected and self.readback_pv.connected

    @raise_if_disconnected
    def get(self, *args, **kwargs):
        # pass through to the readback PV
        return self.readback_pv.get(*args, **kwargs)

    @raise_if_disconnected
    def put(self, *args, **kwargs):
        # pass through to the writeable PV -- potentially could check limits
        # TODO: Should this return a StatusObj?
        return self.pv.put(*args, **kwargs)


class Signal:
    "A descriptor representing a single read-only PV"
    _class = EpicsSignalLite  # type of object returned by __get__

    def __init__(self, pv_template, *args, **kwargs):
        if '{base_name}' not in pv_template:
            raise ValueError("pv_template must contain '{base_name}'")
        self.pv_template = pv_template
        self.args = args
        self.kwargs = kwargs

    def __get__(self, instance, owner):
        if instance is None:
            return
        pv_name = self.pv_template.format(base_name=instance.base_name)
        if pv_name not in instance._pvs:
            # This PV is being read for the first time. Connect.
            instance._pvs[pv_name] = self._class(pv_name,
                                                 *self.args, **self.kwargs)
        return instance._pvs[pv_name]


class SignalRW(Signal):
    "A descriptor representing a writeable PV with a readback value."
    _class = EpicsSignalLiteRW


class SignalMeta(type):
    "Creates attributes for signals by inspecting class definition"
    def __new__(cls, name, bases, clsdict):
        clsobj = super().__new__(cls, name, bases, clsdict)
        # private dict of signal names and signal objects
        sig_dict = {k: v for k, v in clsdict.items() if isinstance(v, Signal)}
        clsobj._templates = {k: v.pv_template for k, v in sig_dict.items()}
        clsobj.signals = sig_dict
        clsobj.signal_names = list(sig_dict.keys())
        # design a default signature for the set method
        writable_signals = [k for k, v in clsobj.signals.items()
                            if isinstance(v, SignalRW)]
        signature = Signature([Parameter(name, Parameter.POSITIONAL_OR_KEYWORD)
                              for name in writable_signals])
        clsobj._default_sig_for_set = signature
        # public accessor of signal objects
        clsobj._pvs = {}
        return clsobj


class Base(metaclass=SignalMeta):
    """
    Base class for hardware objects

    This class provides attribute access to one or more Signals, which can be
    a mixture of read-only and writable. All must share the same base_name.
    """
    def __init__(self, base_name, read_fields=None):
        self.base_name = base_name
        if read_fields is None:
            self.read_fields = self.signal_names

    def read(self):
        # map names ("data keys") to actual values
        return {name: getattr(self, name).get() for name in self.read_fields}

    def describe(self):
        return {name: {'source': getattr(self, name).pv.pvname}
                for name in self.read_fields}

    def stop(self):
        "to be defined by subclass"
        pass

    def trigger(self):
        "to be defined by subclass"
        pass


class SettableBase(Base):
    """
    Base class for hardware objects that can be set

    The entire purpose of this base class is to provide an auto-generated set
    method with a signature that provides one argument per writable signal.

    If you want a different signature, you
    need to write you own set, and this subclass adds no utility.
    """
    def set(self, *args, **kwargs):
        bound = self._default_sig_for_set.bind(*args, **kwargs)
        status_objs = []
        for name, val in bound.arguments.items():
            sig = getattr(self, name)
            # TODO : Will sig.put return a status object?
            status_objs.append(sig.put(val))
        return status_objs


class Motor(Base):
    val = SignalRW('{base_name}.VAL')
    val = SignalRW('{base_name}.VAL')
    egu = SignalRW('{base_name}.EGU')
    movn = SignalRW('{base_name}.MOVN')
    dmov = SignalRW('{base_name}.DMOV')
    stop = SignalRW('{base_name}.STOP')
    


def name_from_pv(pv):
    '''Create a signal's ophyd name based on the PV'''
    name = pv.lower().rstrip(':')
    name = name.replace(':', '.')
    return name


def lookup_doc(cls_, pv):
    '''Lookup documentation extracted from the areadetector html docs

    Go from top-level to base-level class, looking up html documentation
    until we get a hit.

    .. note:: This is only executed once, per class, per property (see ADSignal
        for more information)
    '''
    classes = inspect.getmro(cls_)

    for class_ in classes:
        try:
            html_file = class_._html_docs
        except AttributeError:
            continue

        for fn in html_file:
            try:
                doc = docs.docs[fn]
            except KeyError:
                continue

            try:
                return doc[pv]
            except KeyError:
                pass

            if pv.endswith('_RBV'):
                try:
                    return doc[pv[:-4]]
                except KeyError:
                    pass

    return 'No documentation found [PV suffix=%s]' % pv


class ADSignal(object):
    '''A property-like descriptor

    This descriptor only creates an EpicsSignal instance when it's first
    accessed and not on initialization.

    Optionally, the prefix/suffix can include information from the instance the
    ADSignal is on. On access, the combined prefix and suffix string are
    formatted with str.format().

    Parameters
    ----------
    pv : str
        The suffix portion of the PV.
    has_rbv : bool, optional
        Whether or not a separate readback value pv exists
    doc : str, optional
        Docstring information

    Attributes
    ----------
    pv : str
        The unformatted suffix portion of the PV
    has_rbv : bool, optional
        Whether or not a separate readback value pv exists
    doc : str, optional
        Docstring information

    Examples
    --------

    >>> class SomeDetector(ADBase):
    >>>     signal = ADSignal('Ch{self.channel}', rw=False)
    >>>     enable = ADSignal('enable')
    >>>
    >>>     def __init__(self, prefix, channel=3, **kwargs):
    >>>         super(SomeDetector, self).__init__(prefix, **kwargs)
    >>>         self.channel = channel
    >>>
    >>> test = SomeDetector('my_prefix:')
    >>> print(test.signal)
    EpicsSignal(name='my_prefix.ch3', read_pv='my_prefix:Ch3', rw=False,
                string=False, limits=False, put_complete=False, pv_kw={},
                auto_monitor=None)

    Only at the last line was the signal instantiated. Note how the channel
    information from the object was formatted into the final PV string:
        {prefix}{suffix} -> {prefix}Ch{self.channel} -> my_prefix:Ch3
    '''

    def __init__(self, pv, has_rbv=False, doc=None, **kwargs):
        self.pv = pv
        self.has_rbv = has_rbv
        self.doc = doc
        self.kwargs = kwargs

        self.__doc__ = '[Lazy property for %s]' % pv

    def lookup_doc(self, cls_):
        return lookup_doc(cls_, self.pv)

    def update_docstring(self, cls_):
        if self.doc is None:
            self.__doc__ = self.lookup_doc(cls_)

    def check_exists(self, obj):
        '''Instantiate the signal if necessary'''
        if obj is None:
            # Happens when working on the class and not the object
            return self

        pv = self.pv.format(self=obj)
        try:
            return obj._ad_signals[pv]
        except KeyError:
            base_name = obj.name
            full_name = '%s.%s' % (base_name, name_from_pv(pv))

            read_ = write = ''.join([obj._prefix, pv])

            if self.has_rbv:
                read_ += '_RBV'
            else:
                write = None

            signal = oesig(read_, write_pv=write,
                           name=full_name,
                           **self.kwargs)

            obj._ad_signals[pv] = signal

            if self.doc is not None:
                signal.__doc__ = self.doc
            else:
                signal.__doc__ = self.__doc__

            return obj._ad_signals[pv]

    def __get__(self, obj, objtype=None):
        return self.check_exists(obj)

    def __set__(self, obj, value):
        signal = self.check_exists(obj)
        signal.value = value


def ADSignalGroup(*props, **kwargs):
    def check_exists(self):
        signals = tuple(prop.__get__(self) for prop in props)
        key = tuple(signal.pvname for signal in signals)
        try:
            return self._ad_signals[key]
        except KeyError:
            sg = osiggrp(**kwargs)
            for signal in signals:
                sg.add_signal(signal)

            self._ad_signals[key] = sg
            return self._ad_signals[key]

    def fget(self):
        return check_exists(self)

    def fset(self, value):
        sg = check_exists(self)
        sg.value = value

    doc = kwargs.pop('doc', '')
    return property(fget, fset, doc=doc)


class ADBase:
    '''The AreaDetector base class'''

    _html_docs = ['areaDetectorDoc.html']

    @classmethod
    def _all_adsignals(cls_):
        attrs = [(attr, getattr(cls_, attr))
                 for attr in sorted(dir(cls_))]

        return [(attr, obj) for attr, obj in attrs
                if isinstance(obj, ADSignal)]

    @classmethod
    def _update_docstrings(cls_):
        '''Updates docstrings'''
        for prop_name, signal in cls_._all_adsignals():
            signal.update_docstring(cls_)

    def find_signal(self, text, use_re=False,
                    case_sensitive=False, match_fcn=None,
                    f=sys.stdout):
        '''Search through the signals on this detector for the string text

        Parameters
        ----------
        text : str
            Text to find
        use_re : bool, optional
            Use regular expressions
        case_sensitive : bool, optional
            Case sensitive search
        match_fcn : callable, optional
            Function to call when matches are found Defaults to a function that
            prints matches to f
        f : file-like, optional
            File-like object that the default match function prints to
            (Defaults to sys.stdout)
        '''
        # TODO: Some docstrings change based on the detector type,
        #       showing different options than are available in
        #       the base area detector class (for example). As such,
        #       instead of using the current docstrings, this grabs
        #       them again.
        cls_ = self.__class__

        def default_match(prop_name, signal, doc):
            print('Property: %s' % prop_name)
            if signal.has_rbv:
                print('  Signal: {0} / {0}_RBV'.format(signal.pv, signal.pv))
            else:
                print('  Signal: %s' % (signal.pv))
            print('     Doc: %s' % doc)
            print()

        if match_fcn is None:
            match_fcn = default_match

        if use_re:
            flags = re.MULTILINE
            if not case_sensitive:
                flags |= re.IGNORECASE

            regex = re.compile(text, flags=flags)

        elif not case_sensitive:
            text = text.lower()

        for prop_name, signal in cls_._all_adsignals():
            doc = signal.lookup_doc(cls_)

            if use_re:
                if regex.search(doc):
                    match_fcn(prop_name, signal, doc)
            else:
                if not case_sensitive:
                    if text in doc.lower():
                        match_fcn(prop_name, signal, doc)
                elif text in doc:
                    match_fcn(prop_name, signal, doc)

    @property
    def signals(self):
        '''A dictionary of all signals (or groups) in the object.

        .. note:: Instantiates all lazy signals
        '''
        def safe_getattr(obj, attr):
            try:
                return getattr(obj, attr)
            except:
                return None

        if self.__sig_dict is None:
            attrs = [(attr, safe_getattr(self, attr))
                     for attr in sorted(dir(self))
                     if not attr.startswith('_') and attr != 'signals']

            self.__sig_dict = dict((name, value) for name, value in attrs
                                   if isinstance(value, (osig, osiggrp)))

        return self.__sig_dict

    def __init__(self, prefix, **kwargs):
        name = kwargs.get('name', name_from_pv(prefix))
        alias = kwargs.get('alias', 'None')
        self.name = name
        self.alias = alias

        self._prefix = prefix
        self._ad_signals = {}
        self.__sig_dict = None

    def read(self):
        return self.report()

    @property
    def report(self):
        # TODO: what should this return?
        return {self.name: 0}


class NDArrayDriver(ADBase):
    _html_docs = ['areaDetectorDoc.html']

    array_counter = ADSignal('ArrayCounter', has_rbv=True)
    array_rate = ADSignal('ArrayRate_RBV', rw=False)
    asyn_io = ADSignal('AsynIO')

    nd_attributes_file = ADSignal('NDAttributesFile', string=True)
    pool_alloc_buffers = ADSignal('PoolAllocBuffers')
    pool_free_buffers = ADSignal('PoolFreeBuffers')
    pool_max_buffers = ADSignal('PoolMaxBuffers')
    pool_max_mem = ADSignal('PoolMaxMem')
    pool_used_buffers = ADSignal('PoolUsedBuffers')
    pool_used_mem = ADSignal('PoolUsedMem')
    port_name = ADSignal('PortName_RBV', rw=False, string=True)
