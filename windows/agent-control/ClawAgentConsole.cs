using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace ClawAgentControl
{
    public sealed class ConsoleSnapshot
    {
        public string CurrentLine { get; set; }
        public string Tail { get; set; }
        public int CursorX { get; set; }
        public int CursorY { get; set; }
        public int Width { get; set; }
    }

    public static class NativeConsole
    {
        private const short KeyEvent = 0x0001;
        private const uint CreateNewConsole = 0x00000010;
        private const uint GenericRead = 0x80000000;
        private const uint GenericWrite = 0x40000000;
        private const uint FileShareRead = 0x00000001;
        private const uint FileShareWrite = 0x00000002;
        private const uint OpenExisting = 3;
        private const ushort VkEscape = 0x1B;
        private const ushort VkReturn = 0x0D;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct StartupInfo
        {
            public int cb;
            public string lpReserved;
            public string lpDesktop;
            public string lpTitle;
            public int dwX;
            public int dwY;
            public int dwXSize;
            public int dwYSize;
            public int dwXCountChars;
            public int dwYCountChars;
            public int dwFillAttribute;
            public int dwFlags;
            public short wShowWindow;
            public short cbReserved2;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInformation
        {
            public IntPtr hProcess;
            public IntPtr hThread;
            public uint dwProcessId;
            public uint dwThreadId;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct Coord
        {
            public short X;
            public short Y;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SmallRect
        {
            public short Left;
            public short Top;
            public short Right;
            public short Bottom;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ConsoleScreenBufferInfo
        {
            public Coord dwSize;
            public Coord dwCursorPosition;
            public ushort wAttributes;
            public SmallRect srWindow;
            public Coord dwMaximumWindowSize;
        }

        [StructLayout(LayoutKind.Explicit, CharSet = CharSet.Unicode)]
        private struct KeyEventRecord
        {
            [FieldOffset(0)] public int bKeyDown;
            [FieldOffset(4)] public ushort wRepeatCount;
            [FieldOffset(6)] public ushort wVirtualKeyCode;
            [FieldOffset(8)] public ushort wVirtualScanCode;
            [FieldOffset(10)] public char UnicodeChar;
            [FieldOffset(12)] public uint dwControlKeyState;
        }

        [StructLayout(LayoutKind.Explicit, CharSet = CharSet.Unicode)]
        private struct InputRecord
        {
            [FieldOffset(0)] public short EventType;
            [FieldOffset(4)] public KeyEventRecord KeyEvent;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcessW(
            string applicationName,
            StringBuilder commandLine,
            IntPtr processAttributes,
            IntPtr threadAttributes,
            bool inheritHandles,
            uint creationFlags,
            IntPtr environment,
            string currentDirectory,
            ref StartupInfo startupInfo,
            out ProcessInformation processInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AttachConsole(uint processId);

        [DllImport("kernel32.dll")]
        private static extern bool FreeConsole();

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool WriteConsoleInputW(
            IntPtr consoleInput,
            InputRecord[] buffer,
            uint length,
            out uint written);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetConsoleScreenBufferInfo(
            IntPtr consoleOutput,
            out ConsoleScreenBufferInfo info);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool ReadConsoleOutputCharacterW(
            IntPtr consoleOutput,
            StringBuilder characters,
            uint length,
            Coord readCoordinate,
            out uint read);

        [DllImport("kernel32.dll")]
        private static extern bool CloseHandle(IntPtr handle);

        public static uint StartInNewConsole(string executable, string arguments, string workingDirectory)
        {
            var startup = new StartupInfo { cb = Marshal.SizeOf(typeof(StartupInfo)) };
            ProcessInformation process;
            var commandLine = new StringBuilder(Quote(executable) + " " + arguments);
            if (!CreateProcessW(
                    executable,
                    commandLine,
                    IntPtr.Zero,
                    IntPtr.Zero,
                    false,
                    CreateNewConsole,
                    IntPtr.Zero,
                    workingDirectory,
                    ref startup,
                    out process))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }

            CloseHandle(process.hThread);
            CloseHandle(process.hProcess);
            return process.dwProcessId;
        }

        public static void SendText(uint processId, string text, bool appendEnter)
        {
            WithConsole(processId, delegate
            {
                var input = OpenConsoleDevice("CONIN$");
                try
                {
                    var fullText = appendEnter ? text + "\r" : text;
                    var records = new InputRecord[fullText.Length * 2];
                    var index = 0;
                    foreach (var character in fullText)
                    {
                        var virtualKey = character == '\r' ? VkReturn : (ushort)0;
                        records[index++] = MakeKeyRecord(true, virtualKey, character);
                        records[index++] = MakeKeyRecord(false, virtualKey, character);
                    }

                    uint written;
                    if (!WriteConsoleInputW(input, records, (uint)records.Length, out written) || written != records.Length)
                    {
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not inject text into the Claw console.");
                    }
                }
                finally
                {
                    CloseHandle(input);
                }
            });
        }

        public static void SendEscape(uint processId)
        {
            WithConsole(processId, delegate
            {
                var input = OpenConsoleDevice("CONIN$");
                try
                {
                    var records = new[]
                    {
                        MakeKeyRecord(true, VkEscape, (char)27, 0x01),
                        MakeKeyRecord(false, VkEscape, (char)27, 0x01)
                    };
                    uint written;
                    if (!WriteConsoleInputW(input, records, (uint)records.Length, out written) || written != records.Length)
                    {
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not inject Escape into the Claw console.");
                    }
                }
                finally
                {
                    CloseHandle(input);
                }
            });
        }

        public static ConsoleSnapshot Capture(uint processId, int requestedLines)
        {
            ConsoleSnapshot snapshot = null;
            WithConsole(processId, delegate
            {
                var output = OpenConsoleDevice("CONOUT$");
                try
                {
                    ConsoleScreenBufferInfo info;
                    if (!GetConsoleScreenBufferInfo(output, out info))
                    {
                        throw new Win32Exception(Marshal.GetLastWin32Error());
                    }

                    var width = Math.Max(1, (int)info.dwSize.X);
                    var endY = Math.Max(0, (int)info.dwCursorPosition.Y);
                    var lineCount = Math.Max(1, Math.Min(requestedLines, endY + 1));
                    var startY = endY - lineCount + 1;
                    var characterCount = checked(width * lineCount);
                    var text = new StringBuilder(characterCount);
                    uint read;
                    if (!ReadConsoleOutputCharacterW(
                            output,
                            text,
                            (uint)characterCount,
                            new Coord { X = 0, Y = (short)startY },
                            out read))
                    {
                        throw new Win32Exception(Marshal.GetLastWin32Error());
                    }

                    var raw = text.ToString(0, (int)read);
                    var lines = new string[lineCount];
                    for (var line = 0; line < lineCount; line++)
                    {
                        var offset = line * width;
                        var available = Math.Min(width, Math.Max(0, raw.Length - offset));
                        lines[line] = available == 0
                            ? string.Empty
                            : raw.Substring(offset, available).TrimEnd(' ', '\0');
                    }

                    snapshot = new ConsoleSnapshot
                    {
                        CurrentLine = lines[lines.Length - 1],
                        Tail = string.Join(Environment.NewLine, lines).TrimEnd(),
                        CursorX = info.dwCursorPosition.X,
                        CursorY = info.dwCursorPosition.Y,
                        Width = width
                    };
                }
                finally
                {
                    CloseHandle(output);
                }
            });
            return snapshot;
        }

        private static void WithConsole(uint processId, Action action)
        {
            FreeConsole();
            if (!AttachConsole(processId))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not attach to the Claw console.");
            }

            try
            {
                action();
            }
            finally
            {
                FreeConsole();
            }
        }

        private static InputRecord MakeKeyRecord(bool down, ushort virtualKey, char character, ushort scanCode = 0)
        {
            return new InputRecord
            {
                EventType = KeyEvent,
                KeyEvent = new KeyEventRecord
                {
                    bKeyDown = down ? 1 : 0,
                    wRepeatCount = 1,
                    wVirtualKeyCode = virtualKey,
                    wVirtualScanCode = scanCode,
                    UnicodeChar = character
                }
            };
        }

        private static IntPtr OpenConsoleDevice(string name)
        {
            var handle = CreateFileW(
                name,
                GenericRead | GenericWrite,
                FileShareRead | FileShareWrite,
                IntPtr.Zero,
                OpenExisting,
                0,
                IntPtr.Zero);
            if (handle == IntPtr.Zero || handle == new IntPtr(-1))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "The target process does not own a classic Windows console.");
            }
            return handle;
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }
    }
}
