using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("BABEL Scribe")]
[assembly: AssemblyProduct("BABEL Scribe")]
[assembly: AssemblyVersion("0.1.0.0")]
[assembly: AssemblyFileVersion("0.1.0.0")]

internal static class Launcher
{
    [STAThread]
    private static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        try
        {
            using (Process child = Process.Start(new ProcessStartInfo {
                FileName = python,
                Arguments = "-B -m scribe",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            })) {}
            return 0;
        }
        catch (Exception)
        {
            MessageBox.Show("BABEL Scribe 无法启动。请重新运行安装程序。",
                            "BABEL Scribe", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
