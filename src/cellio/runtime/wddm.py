"""Local-video-memory accounting for this process on a single NVIDIA DXGI adapter."""

import ctypes as c
from uuid import UUID

HRESULT = c.c_int32
UINT = c.c_uint32
PVOID = c.c_void_p
GUID = c.c_ubyte * 16


class AdapterDescription(c.Structure):
    _fields_ = [
        ("description", c.c_wchar * 128),
        ("vendor_id", UINT),
        ("device_id", UINT),
        ("subsystem_id", UINT),
        ("revision", UINT),
        ("dedicated_video", c.c_size_t),
        ("dedicated_system", c.c_size_t),
        ("shared_system", c.c_size_t),
        ("luid_low", UINT),
        ("luid_high", c.c_int32),
        ("flags", UINT),
    ]


class MemoryInfo(c.Structure):
    _fields_ = [
        (key, c.c_uint64)
        for key in ("budget", "current_usage", "available", "reserved")
    ]


def method(interface, slot, restype, *argtypes):
    # COM interfaces put the vtable pointer in their first word. Slots are from
    # IDXGIFactory1, IDXGIAdapter1 and IDXGIAdapter3 in dxgi.h / dxgi1_4.h.
    vtable = c.cast(interface, c.POINTER(c.POINTER(PVOID))).contents
    return c.WINFUNCTYPE(restype, PVOID, *argtypes)(vtable[slot])


def release(interface):
    if interface:
        method(interface, 2, UINT)(interface)


class ProcessVideoMemory:
    def __init__(self):
        self.adapter = PVOID()
        self.name = None
        library = c.WinDLL("dxgi", winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        factory_id = GUID.from_buffer_copy(
            UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le
        )
        adapter3_id = GUID.from_buffer_copy(
            UUID("645967a4-1392-4310-a798-8053ce3e93fd").bytes_le
        )
        create = library.CreateDXGIFactory1
        create.argtypes, create.restype = (c.POINTER(GUID), c.POINTER(PVOID)), HRESULT
        factory = PVOID()
        if create(c.byref(factory_id), c.byref(factory)) < 0:
            return
        try:
            enumerate_adapter = method(factory, 12, HRESULT, UINT, c.POINTER(PVOID))
            for index in range(64):
                entry = PVOID()
                if enumerate_adapter(factory, index, c.byref(entry)) < 0:
                    break
                try:
                    description = AdapterDescription()
                    get_description = method(
                        entry, 10, HRESULT, c.POINTER(AdapterDescription)
                    )
                    if (
                        get_description(entry, c.byref(description)) < 0
                        or description.vendor_id != 0x10DE
                    ):
                        continue
                    if self.adapter:
                        # The worker has no adapter-LUID selection contract for two NVIDIA GPUs.
                        self.close()
                        return
                    query_interface = method(
                        entry, 0, HRESULT, c.POINTER(GUID), c.POINTER(PVOID)
                    )
                    if (
                        query_interface(
                            entry, c.byref(adapter3_id), c.byref(self.adapter)
                        )
                        >= 0
                    ):
                        self.name = description.description
                finally:
                    release(entry)
        finally:
            release(factory)

    def read(self):
        if not self.adapter:
            return None
        info = MemoryInfo()
        query = method(self.adapter, 14, HRESULT, UINT, UINT, c.POINTER(MemoryInfo))
        # Node zero, DXGI_MEMORY_SEGMENT_GROUP_LOCAL. CurrentUsage is for this process.
        return (
            info.current_usage
            if query(self.adapter, 0, 0, c.byref(info)) >= 0
            else None
        )

    def close(self):
        release(self.adapter)
        self.adapter = PVOID()
        self.name = None


class DeviceMemory:
    """NVIDIA whole-device allocation from the driver-provided System32 NVML."""

    def __init__(self):
        self.library = c.WinDLL("nvml.dll", winmode=0x800)
        self.handle = PVOID()
        self.library.nvmlInit_v2.argtypes = ()
        self.library.nvmlShutdown.argtypes = ()
        self.library.nvmlDeviceGetCount_v2.argtypes = (c.POINTER(UINT),)
        self.library.nvmlDeviceGetHandleByIndex_v2.argtypes = (UINT, c.POINTER(PVOID))
        self.library.nvmlDeviceGetMemoryInfo.argtypes = (
            PVOID,
            c.POINTER(c.c_uint64 * 3),
        )
        self.initialized = self.library.nvmlInit_v2() == 0
        count = UINT()
        if (
            self.initialized
            and self.library.nvmlDeviceGetCount_v2(c.byref(count)) == 0
            and count.value == 1
        ):
            self.library.nvmlDeviceGetHandleByIndex_v2(0, c.byref(self.handle))

    def read(self):
        # nvmlMemory_t lays out total, free and used (each uint64, bytes).
        info = (c.c_uint64 * 3)()
        return (
            info[2]
            if self.handle
            and self.library.nvmlDeviceGetMemoryInfo(self.handle, c.byref(info)) == 0
            else None
        )

    def close(self):
        if self.initialized:
            self.library.nvmlShutdown()
            self.initialized = False
