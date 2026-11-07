"""执行请求内的有界缓存；不持有 NetCDF 句柄，不改变落盘与完成标记。"""
from collections import OrderedDict
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import sys

import numpy as np

from .locking import file_lock


def _size(value):
    if isinstance(value, np.ndarray):
        return sys.getsizeof(value) + value.nbytes
    if isinstance(value, dict):
        return sys.getsizeof(value) + sum(_size(k) + _size(v) for k,v in value.items())
    if isinstance(value, (tuple,list)):
        return sys.getsizeof(value) + sum(_size(v) for v in value)
    return sys.getsizeof(value)


class SessionStorage:
    """代理原存储，缓存命中也持读锁并核对文件身份；返回值彼此隔离。"""
    def __init__(self, storage, *, max_bytes=256*1024*1024):
        if isinstance(max_bytes,bool) or not isinstance(max_bytes,int) or max_bytes < 0:
            raise ValueError('processing_cache_bytes 必须是非负整数字节数')
        self.base = storage
        self.max_bytes = max_bytes
        self.cache_bytes = 0
        self.peak_bytes = 0
        self._entries = OrderedDict()
        self._array_keys = set()
        self._scope = None
        self._generations = {}
        self._previous_coordinates = getattr(storage, '_coordinate_cache', None)
        storage._coordinate_cache = self

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _signature(self, path):
        path = Path(path)
        try:
            st = path.stat()
            state = (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
        except FileNotFoundError:
            state = None
        return (state,self._generations.get(path,0))

    def _get(self, key):
        if key not in self._entries:
            return False, None
        value, size = self._entries.pop(key)
        self._entries[key] = (value,size)
        return True, deepcopy(value)

    def _put(self, key, value):
        size = _size(key) + _size(value)
        if size > self.max_bytes:
            return
        if key in self._entries:
            _, old_size = self._entries.pop(key)
            self.cache_bytes -= old_size
        while self._entries and self.cache_bytes + size > self.max_bytes:
            old_key, (_,old_size) = self._entries.popitem(last=False)
            self._array_keys.discard(old_key)
            self.cache_bytes -= old_size
        self._entries[key] = (deepcopy(value),size)
        if key[0] in ('load','calculation','geometry'):
            self._array_keys.add(key)
        self.cache_bytes += size
        self.peak_bytes = max(self.peak_bytes,self.cache_bytes)

    def begin_scope(self, scope):
        """跨成员/域/时次释放大数组与剖面，小型元数据仍受统一LRU上限约束。"""
        if scope == self._scope:
            return
        for key in self._array_keys:
            _, size = self._entries.pop(key)
            self.cache_bytes -= size
        self._array_keys.clear()
        self._scope = scope

    def _read(self, kind, task):
        path = self.base.path_for(task)
        with file_lock(path,exclusive=False,timeout_seconds=self.base.lock_timeout_seconds):
            signature = self._signature(path)
            key = (kind,task,signature)
            hit,value = self._get(key)
            if hit:
                return value
            value = getattr(self.base,kind)(task)
            self._put(key,value)
            if kind == 'load':
                self._put(('shape',task,signature),tuple(np.shape(value)))
            if kind in ('load','shape') or (kind == 'load_metadata' and value):
                self._put(('exists',task,signature),True)
            return value

    def load(self, task):
        return self._read('load',task)

    def load_metadata(self, task):
        return self._read('load_metadata',task)

    def shape(self, task):
        return self._read('shape',task)

    def remember_shape(self, task, shape):
        # 由底层在同一次读锁内、确认completed后调用。
        signature = self._signature(self.base.path_for(task))
        self._put(('shape',task,signature),tuple(shape))

    def exists(self, task):
        return self._read('exists',task)

    def fingerprint(self, task):
        path = self.base.path_for(task)
        with file_lock(path,exclusive=False,timeout_seconds=self.base.lock_timeout_seconds):
            try:
                st = path.stat()
            except FileNotFoundError:
                return {'data': None}
            return {'data':{'size':st.st_size,'mtime_ns':st.st_mtime_ns},
                    'slice':{'time_minutes':task.time_minutes,'height_m':task.height_m,'completed':self.exists(task)}}

    def save(self, task, *args, **kwargs):
        path = self.base.path_for(task)
        self._generations[path] = self._generations.get(path,0) + 1
        try:
            return self.base.save(task,*args,**kwargs)
        finally:
            # 失败时也可能已写入未完成标记，必须使旧完成状态和数据失效。
            self._generations[path] += 1

    def save_many(self, items):
        """批量写（2026-11-03）：与逐个 save 一样，写前写后都让涉及文件的缓存失效。"""
        items = list(items)
        paths = {self.base.path_for(item[0]) for item in items}
        for path in paths:
            self._generations[path] = self._generations.get(path,0) + 1
        try:
            return self.base.save_many(items)
        finally:
            for path in paths:
                self._generations[path] += 1

    def coordinate_values(self, dataset, name):
        path = Path(dataset.filepath())
        key = ('coordinates',path,name,self._signature(path))
        hit,values = self._get(key)
        if not hit:
            values = self.base._unique_coordinate(dataset,name)
            self._put(key,values)
        return values

    def cached_calculation(self, key, dependencies, factory):
        paths = sorted({self.base.path_for(task) for task in dependencies})
        # 在一次纯计算期间持有输入共享锁，防止其他遵守锁的写入者改变输入。
        with ExitStack() as locks:
            for path in paths:
                locks.enter_context(file_lock(path,exclusive=False,timeout_seconds=self.base.lock_timeout_seconds))
            identity = tuple((task,self._signature(self.base.path_for(task))) for task in dependencies)
            cache_key = ('calculation',key,identity)
            hit,value = self._get(cache_key)
            if hit:
                return value
            value = factory()
            self._put(cache_key,value)
            return value

    def cached_geometry(self, key, factory):
        cache_key = ('geometry', key)
        if cache_key in self._entries:
            value, _ = self._entries[cache_key]
            self._entries.move_to_end(cache_key)
        else:
            value = factory()
            self._put(cache_key, value)
            if cache_key in self._entries:
                value = self._entries[cache_key][0]
            else:
                value = deepcopy(value)
        return self._geometry_view(value)

    @staticmethod
    def _geometry_view(value):
        # 只共享不可写几何坐标；普通数据数组仍保持独立副本。
        if isinstance(value, np.ndarray):
            value.setflags(write=False)
            return value.view()
        if isinstance(value, tuple):
            return tuple(SessionStorage._geometry_view(item) for item in value)
        return deepcopy(value)

    def clear(self):
        self._entries.clear()
        self._array_keys.clear()
        self._scope = None
        self._generations.clear()
        self.cache_bytes = 0
        if getattr(self.base,'_coordinate_cache',None) is self:
            self.base._coordinate_cache = self._previous_coordinates
