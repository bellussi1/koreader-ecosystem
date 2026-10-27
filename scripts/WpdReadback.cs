using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Security.Cryptography;

[StructLayout(LayoutKind.Sequential)]
public struct KrePropertyKey
{
    public Guid fmtid; public uint pid;
    public KrePropertyKey(string format, uint propertyId) { fmtid = new Guid(format); pid = propertyId; }
}
[ComImport, Guid("a1567595-4c2f-4574-a6fa-ecef917b9a40"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface KrePortableDeviceManager
{
    [PreserveSig] int GetDevices(IntPtr ids, ref uint count);
    [PreserveSig] int RefreshDeviceList();
    [PreserveSig] int GetDeviceFriendlyName([MarshalAs(UnmanagedType.LPWStr)] string id, IntPtr name, ref uint chars);
    [PreserveSig] int GetDeviceDescription([MarshalAs(UnmanagedType.LPWStr)] string id, IntPtr name, ref uint chars);
}
[ComImport, Guid("0af10cec-2ecd-4b92-9581-34f6ae0637f3")] public class KrePortableDeviceManagerClass { }
[ComImport, Guid("f7c0039a-4762-488a-b4b3-760ef9a1ba9b")] public class KrePortableDeviceFtmClass { }
[ComImport, Guid("0c15d503-d017-47ce-9016-7b3f978721cc")] public class KrePortableDeviceValuesClass { }

[ComImport, Guid("6848f6f2-3155-4f86-b6f5-263eeeab3143"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface KrePortableDeviceValues
{
    [PreserveSig] int GetCount(out uint count);
    [PreserveSig] int GetAt(uint index, IntPtr key, IntPtr value);
    [PreserveSig] int SetValue(ref KrePropertyKey key, IntPtr value);
    [PreserveSig] int GetValue(ref KrePropertyKey key, IntPtr value);
    [PreserveSig] int SetStringValue(ref KrePropertyKey key, [MarshalAs(UnmanagedType.LPWStr)] string value);
    [PreserveSig] int GetStringValue(ref KrePropertyKey key, out IntPtr value);
    [PreserveSig] int SetUnsignedIntegerValue(ref KrePropertyKey key, uint value);
    [PreserveSig] int GetUnsignedIntegerValue(ref KrePropertyKey key, out uint value);
    [PreserveSig] int SetSignedIntegerValue(ref KrePropertyKey key, int value);
    [PreserveSig] int GetSignedIntegerValue(ref KrePropertyKey key, out int value);
    [PreserveSig] int SetUnsignedLargeIntegerValue(ref KrePropertyKey key, ulong value);
    [PreserveSig] int GetUnsignedLargeIntegerValue(ref KrePropertyKey key, out ulong value);
    [PreserveSig] int SetSignedLargeIntegerValue(ref KrePropertyKey key, long value);
    [PreserveSig] int GetSignedLargeIntegerValue(ref KrePropertyKey key, out long value);
    [PreserveSig] int SetFloatValue(ref KrePropertyKey key, float value);
    [PreserveSig] int GetFloatValue(ref KrePropertyKey key, out float value);
    [PreserveSig] int SetErrorValue(ref KrePropertyKey key, int value);
    [PreserveSig] int GetErrorValue(ref KrePropertyKey key, out int value);
    [PreserveSig] int SetKeyValue(ref KrePropertyKey key, ref KrePropertyKey value);
    [PreserveSig] int GetKeyValue(ref KrePropertyKey key, out KrePropertyKey value);
    [PreserveSig] int SetBoolValue(ref KrePropertyKey key, [MarshalAs(UnmanagedType.Bool)] bool value);
    [PreserveSig] int GetBoolValue(ref KrePropertyKey key, [MarshalAs(UnmanagedType.Bool)] out bool value);
    [PreserveSig] int SetIUnknownValue(ref KrePropertyKey key, [MarshalAs(UnmanagedType.IUnknown)] object value);
    [PreserveSig] int GetIUnknownValue(ref KrePropertyKey key, [MarshalAs(UnmanagedType.IUnknown)] out object value);
    [PreserveSig] int SetGuidValue(ref KrePropertyKey key, ref Guid value);
    [PreserveSig] int GetGuidValue(ref KrePropertyKey key, out Guid value);
}
[ComImport, Guid("625e2df8-6392-4cf0-9ad1-3cfa5f17775c"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface KrePortableDevice
{
    [PreserveSig] int Open([MarshalAs(UnmanagedType.LPWStr)] string id, KrePortableDeviceValues info);
    [PreserveSig] int SendCommand(uint flags, KrePortableDeviceValues parameters, out KrePortableDeviceValues results);
    [PreserveSig] int Content(out KrePortableDeviceContent content);
    [PreserveSig] int Capabilities(out IntPtr capabilities);
    [PreserveSig] int Cancel();
    [PreserveSig] int Close();
}
[ComImport, Guid("6a96ed84-7c73-4480-9938-bf5af477d426"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface KrePortableDeviceContent
{
    [PreserveSig] int EnumObjects(uint flags, [MarshalAs(UnmanagedType.LPWStr)] string parentId, KrePortableDeviceValues filter, out KreEnumObjectIds ids);
    [PreserveSig] int Properties(out KrePortableDeviceProperties properties);
    [PreserveSig] int Transfer(out KrePortableDeviceResources resources);
}
[ComImport, Guid("10ece955-cf41-4728-bfa0-41eedf1bbf19"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface KreEnumObjectIds { [PreserveSig] int Next(uint count, IntPtr ids, ref uint fetched); }
[ComImport, Guid("7f6d695c-03df-4439-a809-59266beee3a6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface KrePortableDeviceProperties
{
    [PreserveSig] int GetSupportedProperties([MarshalAs(UnmanagedType.LPWStr)] string objectId, out IntPtr keys);
    [PreserveSig] int GetPropertyAttributes([MarshalAs(UnmanagedType.LPWStr)] string objectId, ref KrePropertyKey key, out KrePortableDeviceValues attributes);
    [PreserveSig] int GetValues([MarshalAs(UnmanagedType.LPWStr)] string objectId, IntPtr keys, out KrePortableDeviceValues values);
}
[ComImport, Guid("fd8878ac-d841-4d17-891c-e6829cdb6934"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface KrePortableDeviceResources
{
    [PreserveSig] int GetSupportedResources([MarshalAs(UnmanagedType.LPWStr)] string objectId, out IntPtr keys);
    [PreserveSig] int GetResourceAttributes([MarshalAs(UnmanagedType.LPWStr)] string objectId, ref KrePropertyKey key, out KrePortableDeviceValues attributes);
    [PreserveSig] int GetStream([MarshalAs(UnmanagedType.LPWStr)] string objectId, ref KrePropertyKey key, uint mode, ref uint optimalBufferSize, out IStream stream);
}

public sealed class KreWpdReadbackResult
{
    public string Status = "copy_failed";
    public string DiagnosticCode = "unclassified";
    public int DiagnosticIndex = -1;
    public int DiagnosticDepth = -1;
    public string[] Sha256 = new string[0];
}

public static class KreWpdReadback
{
    static readonly KrePropertyKey NameKey = new KrePropertyKey("ef6b490d-5cd8-437a-affc-da8b60ee4a3c", 4);
    static readonly KrePropertyKey TypeKey = new KrePropertyKey("ef6b490d-5cd8-437a-affc-da8b60ee4a3c", 7);
    static readonly KrePropertyKey DefaultResourceKey = new KrePropertyKey("e81e79be-34f0-41bf-b53f-f1a06ae87842", 0);
    static readonly KrePropertyKey ResourceTotalSizeKey = new KrePropertyKey("1eb6f604-9278-429f-93cc-5bb8c06656b6", 2);
    static readonly KrePropertyKey ClientNameKey = new KrePropertyKey("204d9f0c-2292-4080-9f42-40664e70f859", 2);
    static readonly KrePropertyKey ClientMajorKey = new KrePropertyKey("204d9f0c-2292-4080-9f42-40664e70f859", 3);
    static readonly KrePropertyKey ClientMinorKey = new KrePropertyKey("204d9f0c-2292-4080-9f42-40664e70f859", 4);
    static readonly KrePropertyKey ClientRevisionKey = new KrePropertyKey("204d9f0c-2292-4080-9f42-40664e70f859", 5);
    static readonly KrePropertyKey DesiredAccessKey = new KrePropertyKey("204d9f0c-2292-4080-9f42-40664e70f859", 9);
    static readonly Guid FolderType = new Guid("27e2e392-a111-48e0-ab0c-e17705a05f85");
    const uint GenericRead = 0x80000000;
    const uint StgmRead = 0;

    public static bool RequiresShellReadback(string relativePath)
    {
        if (String.IsNullOrWhiteSpace(relativePath)) return false;
        string[] parts = relativePath.Split(new char[] { (char)92 });
        if (!String.Equals(parts[0], "system", StringComparison.OrdinalIgnoreCase) &&
            !String.Equals(parts[0], "koreader", StringComparison.OrdinalIgnoreCase)) return true;
        for (int index = 0; index < parts.Length - 1; index++)
            if (parts[index].EndsWith(".sdr", StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }

    public static bool RequiresWpdReadback(string relativePath, ulong size)
    {
        return size > 0 && !RequiresShellReadback(relativePath);
    }

    public static bool IsPathResolutionFailure(string diagnosticCode)
    {
        return diagnosticCode == "folder_unmatched" || diagnosticCode == "folder_ambiguous" ||
            diagnosticCode == "folder_unicode_normalization" || diagnosticCode == "folder_is_file" ||
            diagnosticCode == "file_name_unmatched" || diagnosticCode == "file_unicode_normalization" ||
            diagnosticCode == "file_compatibility_normalization" || diagnosticCode == "file_size_unmatched" ||
            diagnosticCode == "file_ambiguous";
    }

    public static bool NamesEquivalent(string left, string right)
    {
        if (left == null || right == null) return false;
        try
        {
            return String.Equals(
                left.Normalize(System.Text.NormalizationForm.FormC),
                right.Normalize(System.Text.NormalizationForm.FormC),
                StringComparison.OrdinalIgnoreCase);
        }
        catch (ArgumentException) { return false; }
    }

    sealed class Entry { public string Id; public string Name; public bool IsFolder; }

    static void Check(int hr, string reason)
    {
        if (hr < 0) throw new InvalidOperationException(reason);
    }

    public static T AfterDeviceListRefresh<T>(Func<int> refreshDeviceList, Func<T> enumerate)
    {
        if (refreshDeviceList == null || enumerate == null)
            throw new InvalidOperationException("copy_failed");
        Check(refreshDeviceList(), "copy_failed");
        return enumerate();
    }

    public static T RetryAfterDeviceListRefresh<T>(
        Func<int> refreshDeviceList,
        Func<T> enumerate,
        Func<T, bool> isReady,
        int maxAttempts,
        int delayMilliseconds)
    {
        if (refreshDeviceList == null || enumerate == null || isReady == null ||
            maxAttempts < 1 || maxAttempts > 5 || delayMilliseconds < 0 || delayMilliseconds > 2000)
            throw new InvalidOperationException("copy_failed");

        T result = default(T);
        for (int attempt = 0; attempt < maxAttempts; attempt++)
        {
            result = AfterDeviceListRefresh(refreshDeviceList, enumerate);
            if (isReady(result) || attempt + 1 == maxAttempts) return result;
            if (delayMilliseconds > 0) System.Threading.Thread.Sleep(delayMilliseconds);
        }
        return result;
    }

    static string FriendlyName(KrePortableDeviceManager manager, string id)
    {
        uint chars = 0;
        int hr = manager.GetDeviceFriendlyName(id, IntPtr.Zero, ref chars);
        bool description = false;
        if (hr < 0 || chars == 0)
        {
            chars = 0;
            hr = manager.GetDeviceDescription(id, IntPtr.Zero, ref chars);
            description = true;
        }
        if (hr < 0 || chars == 0) return "";
        IntPtr buffer = Marshal.AllocHGlobal(checked((int)chars * 2));
        try
        {
            hr = description
                ? manager.GetDeviceDescription(id, buffer, ref chars)
                : manager.GetDeviceFriendlyName(id, buffer, ref chars);
            return hr < 0 ? "" : (Marshal.PtrToStringUni(buffer) ?? "");
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    static List<string> ReadDeviceIds(KrePortableDeviceManager manager)
    {
        uint count = 0;
        Check(manager.GetDevices(IntPtr.Zero, ref count), "copy_failed");
        if (count > 32) throw new InvalidOperationException("multiple_devices");
        if (count == 0) return new List<string>();
        IntPtr buffer = Marshal.AllocCoTaskMem(checked((int)count * IntPtr.Size));
        try
        {
            for (int i = 0; i < count; i++) Marshal.WriteIntPtr(buffer, i * IntPtr.Size, IntPtr.Zero);
            Check(manager.GetDevices(buffer, ref count), "copy_failed");
            List<string> result = new List<string>();
            for (int i = 0; i < count; i++)
            {
                IntPtr value = Marshal.ReadIntPtr(buffer, i * IntPtr.Size);
                if (value == IntPtr.Zero) continue;
                result.Add(Marshal.PtrToStringUni(value) ?? "");
                Marshal.FreeCoTaskMem(value);
                Marshal.WriteIntPtr(buffer, i * IntPtr.Size, IntPtr.Zero);
            }
            return result;
        }
        finally { Marshal.FreeCoTaskMem(buffer); }
    }

    static List<string> KindleDeviceIds(KrePortableDeviceManager manager)
    {
        return RetryAfterDeviceListRefresh<List<string>>(
            delegate { return manager.RefreshDeviceList(); },
            delegate
            {
                List<string> kindles = new List<string>();
                foreach (string id in ReadDeviceIds(manager))
                    if (FriendlyName(manager, id).IndexOf("kindle", StringComparison.OrdinalIgnoreCase) >= 0)
                        kindles.Add(id);
                return kindles;
            },
            delegate(List<string> kindles) { return kindles.Count > 0; },
            3,
            500);
    }

    static string ReadString(KrePortableDeviceValues values, KrePropertyKey key)
    {
        IntPtr value = IntPtr.Zero;
        int hr = values.GetStringValue(ref key, out value);
        if (hr < 0 || value == IntPtr.Zero) return "";
        try { return Marshal.PtrToStringUni(value) ?? ""; }
        finally { Marshal.FreeCoTaskMem(value); }
    }

    static List<Entry> GetChildren(
        KrePortableDeviceContent content,
        KrePortableDeviceProperties properties,
        string parentId)
    {
        KreEnumObjectIds enumerator = null;
        Check(content.EnumObjects(0, parentId, null, out enumerator), "source_changed");
        List<Entry> entries = new List<Entry>();
        IntPtr slot = Marshal.AllocCoTaskMem(IntPtr.Size);
        try
        {
            while (true)
            {
                Marshal.WriteIntPtr(slot, IntPtr.Zero);
                uint fetched = 0;
                int hr = enumerator.Next(1, slot, ref fetched);
                if (fetched == 0) break;
                Check(hr, "source_changed");
                IntPtr idPointer = Marshal.ReadIntPtr(slot);
                if (idPointer == IntPtr.Zero) throw new InvalidOperationException("source_changed");
                string id;
                try { id = Marshal.PtrToStringUni(idPointer) ?? ""; }
                finally { Marshal.FreeCoTaskMem(idPointer); }
                KrePortableDeviceValues values = null;
                try
                {
                    Check(properties.GetValues(id, IntPtr.Zero, out values), "source_changed");
                    string name = ReadString(values, NameKey);
                    Guid type = Guid.Empty;
                    KrePropertyKey typeKey = TypeKey;
                    values.GetGuidValue(ref typeKey, out type);
                    entries.Add(new Entry { Id = id, Name = name, IsFolder = type == FolderType });
                    if (entries.Count > 100000) throw new InvalidOperationException("source_changed");
                }
                finally
                {
                    if (values != null && Marshal.IsComObject(values)) Marshal.ReleaseComObject(values);
                }
            }
            return entries;
        }
        finally
        {
            Marshal.FreeCoTaskMem(slot);
            if (enumerator != null && Marshal.IsComObject(enumerator)) Marshal.ReleaseComObject(enumerator);
        }
    }

    static List<Entry> Children(
        KrePortableDeviceContent content,
        KrePortableDeviceProperties properties,
        string parentId,
        Dictionary<string, List<Entry>> cache)
    {
        List<Entry> entries;
        if (cache.TryGetValue(parentId, out entries)) return entries;
        entries = GetChildren(content, properties, parentId);
        cache.Add(parentId, entries);
        return entries;
    }

    static string ReadHash(KrePortableDeviceResources resources, Entry file, ulong expectedSize)
    {
        KrePortableDeviceValues attributes = null;
        IStream stream = null;
        try
        {
            KrePropertyKey resourceKey = DefaultResourceKey;
            Check(resources.GetResourceAttributes(file.Id, ref resourceKey, out attributes), "resource_attributes");
            ulong actualSize = 0;
            KrePropertyKey sizeKey = ResourceTotalSizeKey;
            Check(attributes.GetUnsignedLargeIntegerValue(ref sizeKey, out actualSize), "resource_size");
            if (actualSize != expectedSize) throw new InvalidOperationException("resource_size");
            if (expectedSize == 0)
            {
                using (SHA256 empty = SHA256.Create())
                    return BitConverter.ToString(empty.ComputeHash(new byte[0])).Replace("-", "").ToLowerInvariant();
            }

            uint bufferSize = 65536;
            Check(resources.GetStream(file.Id, ref resourceKey, StgmRead, ref bufferSize, out stream), "resource_stream");
            if (bufferSize < 4096 || bufferSize > 262144) bufferSize = 65536;
            byte[] buffer = new byte[bufferSize];
            IntPtr readCount = Marshal.AllocHGlobal(sizeof(int));
            try
            {
                using (SHA256 hash = SHA256.Create())
                {
                    ulong bytesRead = 0;
                    while (true)
                    {
                        Marshal.WriteInt32(readCount, 0);
                        stream.Read(buffer, buffer.Length, readCount);
                        int count = Marshal.ReadInt32(readCount);
                        if (count < 0 || count > buffer.Length) throw new InvalidOperationException("stream_length");
                        if (count == 0) break;
                        bytesRead += (uint)count;
                        if (bytesRead > expectedSize) throw new InvalidOperationException("stream_length");
                        hash.TransformBlock(buffer, 0, count, buffer, 0);
                    }
                    if (bytesRead != expectedSize) throw new InvalidOperationException("stream_length");
                    hash.TransformFinalBlock(new byte[0], 0, 0);
                    return BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
                }
            }
            finally { Marshal.FreeHGlobal(readCount); }
        }
        finally
        {
            if (stream != null && Marshal.IsComObject(stream)) Marshal.ReleaseComObject(stream);
            if (attributes != null && Marshal.IsComObject(attributes)) Marshal.ReleaseComObject(attributes);
        }
    }

    static bool HasResourceSize(KrePortableDeviceResources resources, Entry file, ulong expectedSize)
    {
        KrePortableDeviceValues attributes = null;
        try
        {
            KrePropertyKey resourceKey = DefaultResourceKey;
            if (resources.GetResourceAttributes(file.Id, ref resourceKey, out attributes) < 0 || attributes == null)
                return false;
            ulong actualSize = 0;
            KrePropertyKey sizeKey = ResourceTotalSizeKey;
            return attributes.GetUnsignedLargeIntegerValue(ref sizeKey, out actualSize) >= 0 && actualSize == expectedSize;
        }
        finally
        {
            if (attributes != null && Marshal.IsComObject(attributes)) Marshal.ReleaseComObject(attributes);
        }
    }

    public static KreWpdReadbackResult ReadFiles(string[] relativePaths, ulong[] expectedSizes)
    {
        KreWpdReadbackResult result = new KreWpdReadbackResult();
        object managerObject = null, deviceObject = null, infoObject = null;
        KrePortableDeviceManager manager = null;
        KrePortableDevice device = null;
        KrePortableDeviceContent content = null;
        KrePortableDeviceProperties properties = null;
        KrePortableDeviceResources resources = null;
        try
        {
            if (relativePaths == null || expectedSizes == null || relativePaths.Length != expectedSizes.Length)
                throw new InvalidOperationException("input_mismatch");
            managerObject = new KrePortableDeviceManagerClass();
            manager = (KrePortableDeviceManager)managerObject;
            List<string> kindles = KindleDeviceIds(manager);
            if (kindles.Count == 0) { result.Status = "device_not_found"; result.DiagnosticCode = result.Status; return result; }
            if (kindles.Count != 1) { result.Status = "multiple_devices"; result.DiagnosticCode = result.Status; return result; }

            infoObject = new KrePortableDeviceValuesClass();
            KrePortableDeviceValues info = (KrePortableDeviceValues)infoObject;
            KrePropertyKey clientName = ClientNameKey, major = ClientMajorKey, minor = ClientMinorKey;
            KrePropertyKey revision = ClientRevisionKey, access = DesiredAccessKey;
            Check(info.SetStringValue(ref clientName, "KOReader Ecosystem read-only verifier"), "copy_failed");
            Check(info.SetUnsignedIntegerValue(ref major, 1), "copy_failed");
            Check(info.SetUnsignedIntegerValue(ref minor, 0), "copy_failed");
            Check(info.SetUnsignedIntegerValue(ref revision, 0), "copy_failed");
            Check(info.SetUnsignedIntegerValue(ref access, GenericRead), "copy_failed");
            deviceObject = new KrePortableDeviceFtmClass();
            device = (KrePortableDevice)deviceObject;
            Check(device.Open(kindles[0], info), "copy_failed");
            Check(device.Content(out content), "copy_failed");
            Check(content.Properties(out properties), "copy_failed");
            Check(content.Transfer(out resources), "copy_failed");

            Dictionary<string, List<Entry>> cache = new Dictionary<string, List<Entry>>(StringComparer.Ordinal);
            List<Entry> root = Children(content, properties, "DEVICE", cache);
            List<Entry> storages = root.FindAll(delegate(Entry entry) {
                return String.Equals(entry.Name, "Internal Storage", StringComparison.OrdinalIgnoreCase);
            });
            if (storages.Count == 0) { result.Status = "storage_not_found"; result.DiagnosticCode = result.Status; return result; }
            if (storages.Count != 1) { result.Status = "multiple_storages"; result.DiagnosticCode = result.Status; return result; }

            string[] hashes = new string[relativePaths.Length];
            HashSet<string> seenPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int index = 0; index < relativePaths.Length; index++)
            {
                result.DiagnosticIndex = index;
                result.DiagnosticDepth = -1;
                string path = relativePaths[index];
                if (String.IsNullOrWhiteSpace(path) || path.IndexOf('/') >= 0 ||
                    path.StartsWith("\\", StringComparison.Ordinal) || path.EndsWith("\\", StringComparison.Ordinal))
                    throw new InvalidOperationException("path_invalid");
                string[] parts = path.Split('\\');
                string currentId = storages[0].Id;
                for (int partIndex = 0; partIndex < parts.Length - 1; partIndex++)
                {
                    if (String.IsNullOrEmpty(parts[partIndex])) throw new InvalidOperationException("path_invalid");
                    List<Entry> children = Children(content, properties, currentId, cache);
                    int parentIndex = partIndex;
                    List<Entry> folders = children.FindAll(delegate(Entry entry) {
                        return entry.IsFolder && String.Equals(entry.Name, parts[parentIndex], StringComparison.OrdinalIgnoreCase);
                    });
                    if (folders.Count > 1)
                    {
                        result.DiagnosticDepth = partIndex;
                        throw new InvalidOperationException("folder_ambiguous");
                    }
                    if (folders.Count == 0)
                    {
                        result.DiagnosticDepth = partIndex;
                        bool normalizedFolder = children.Exists(delegate(Entry entry) {
                            return entry.IsFolder && NamesEquivalent(entry.Name, parts[parentIndex]);
                        });
                        bool fileWithSameName = children.Exists(delegate(Entry entry) {
                            return !entry.IsFolder && String.Equals(entry.Name, parts[parentIndex], StringComparison.OrdinalIgnoreCase);
                        });
                        string reason = normalizedFolder ? "folder_unicode_normalization" :
                            fileWithSameName ? "folder_is_file" : "folder_unmatched";
                        throw new InvalidOperationException(reason);
                    }
                    currentId = folders[0].Id;
                }

                string normalizedPath = String.Join("\\", parts);
                if (!seenPaths.Add(normalizedPath)) throw new InvalidOperationException("path_duplicate");
                result.DiagnosticDepth = parts.Length - 1;
                List<Entry> finalChildren = Children(content, properties, currentId, cache);
                string requestedName = parts[parts.Length - 1];
                string requestedBaseName = Path.GetFileNameWithoutExtension(requestedName);
                List<Entry> nameMatches = finalChildren.FindAll(delegate(Entry entry) {
                    return !entry.IsFolder && (
                        String.Equals(entry.Name, requestedName, StringComparison.OrdinalIgnoreCase) ||
                        String.Equals(Path.GetFileNameWithoutExtension(entry.Name), requestedBaseName, StringComparison.OrdinalIgnoreCase));
                });
                if (nameMatches.Count == 0)
                {
                    bool canonicalName = finalChildren.Exists(delegate(Entry entry) {
                        return !entry.IsFolder && (
                            NamesEquivalent(entry.Name, requestedName) ||
                            NamesEquivalent(Path.GetFileNameWithoutExtension(entry.Name), requestedBaseName));
                    });
                    bool compatibilityName = !canonicalName && finalChildren.Exists(delegate(Entry entry) {
                        if (entry.IsFolder) return false;
                        try
                        {
                            return String.Equals(entry.Name.Normalize(System.Text.NormalizationForm.FormKC),
                                requestedName.Normalize(System.Text.NormalizationForm.FormKC),
                                StringComparison.OrdinalIgnoreCase) ||
                                String.Equals(Path.GetFileNameWithoutExtension(entry.Name).Normalize(System.Text.NormalizationForm.FormKC),
                                    requestedBaseName.Normalize(System.Text.NormalizationForm.FormKC),
                                    StringComparison.OrdinalIgnoreCase);
                        }
                        catch (ArgumentException) { return false; }
                    });
                    string reason = canonicalName ? "file_unicode_normalization" :
                        compatibilityName ? "file_compatibility_normalization" : "file_name_unmatched";
                    throw new InvalidOperationException(reason);
                }
                List<Entry> sizeMatches = nameMatches.FindAll(delegate(Entry entry) {
                    return HasResourceSize(resources, entry, expectedSizes[index]);
                });
                if (sizeMatches.Count == 0) throw new InvalidOperationException("file_size_unmatched");
                if (sizeMatches.Count != 1) throw new InvalidOperationException("file_ambiguous");
                hashes[index] = ReadHash(resources, sizeMatches[0], expectedSizes[index]);
            }
            result.Status = "ok";
            result.DiagnosticCode = "ok";
            result.DiagnosticIndex = -1;
            result.DiagnosticDepth = -1;
            result.Sha256 = hashes;
            return result;
        }
        catch (InvalidOperationException ex)
        {
            string reason = ex.Message;
            result.DiagnosticCode = reason == "input_mismatch" || reason == "path_invalid" ||
                reason == "folder_unmatched" || reason == "folder_ambiguous" ||
                reason == "folder_unicode_normalization" || reason == "folder_is_file" || reason == "path_duplicate" ||
                reason == "file_name_unmatched" || reason == "file_unicode_normalization" ||
                reason == "file_compatibility_normalization" || reason == "file_size_unmatched" ||
                reason == "file_ambiguous" || reason == "resource_attributes" ||
                reason == "resource_size" || reason == "resource_stream" ||
                reason == "stream_length" || reason == "source_changed" ||
                reason == "copy_failed" || reason == "device_not_found" ||
                reason == "multiple_devices" || reason == "storage_not_found" ||
                reason == "multiple_storages" ? reason : "unclassified";
            result.Status = reason == "copy_failed" || reason == "resource_stream"
                ? "copy_failed"
                : reason == "device_not_found" || reason == "multiple_devices" ||
                    reason == "storage_not_found" || reason == "multiple_storages"
                    ? reason : "source_changed";
            return result;
        }
        catch (Exception)
        {
            result.Status = "copy_failed";
            result.DiagnosticCode = "unclassified";
            return result;
        }
        finally
        {
            if (resources != null && Marshal.IsComObject(resources)) Marshal.ReleaseComObject(resources);
            if (properties != null && Marshal.IsComObject(properties)) Marshal.ReleaseComObject(properties);
            if (content != null && Marshal.IsComObject(content)) Marshal.ReleaseComObject(content);
            if (device != null) { try { device.Close(); } catch (Exception) { } }
            if (deviceObject != null && Marshal.IsComObject(deviceObject)) Marshal.FinalReleaseComObject(deviceObject);
            if (infoObject != null && Marshal.IsComObject(infoObject)) Marshal.FinalReleaseComObject(infoObject);
            if (managerObject != null && Marshal.IsComObject(managerObject)) Marshal.FinalReleaseComObject(managerObject);
        }
    }
}
