using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text.Json;
using Windows.Devices.WiFiDirect;
using Windows.Security.Credentials;

namespace QRBeam.SoftAP;

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    public static async Task<int> Main(string[] args)
    {
        try
        {
            var options = ParseArguments(args);
            Validate(options.Ssid, options.Password);

            if (options.ValidateOnly)
            {
                WriteJson(new
                {
                    status = "valid",
                    ssid = options.Ssid,
                    addresses = DiscoverSoftApAddresses()
                });
                return 0;
            }

            using var cancellation = new CancellationTokenSource();
            Console.CancelKeyPress += (_, eventArgs) =>
            {
                eventArgs.Cancel = true;
                cancellation.Cancel();
            };

            var publisher = new WiFiDirectAdvertisementPublisher();
            var advertisement = publisher.Advertisement;
            advertisement.IsAutonomousGroupOwnerEnabled = true;
            advertisement.ListenStateDiscoverability =
                WiFiDirectAdvertisementListenStateDiscoverability.Normal;
            advertisement.LegacySettings.IsEnabled = true;
            advertisement.LegacySettings.Ssid = options.Ssid;
            advertisement.LegacySettings.Passphrase = new PasswordCredential(
                "QRBeam",
                options.Ssid,
                options.Password
            );

            var started = new TaskCompletionSource<WiFiDirectAdvertisementPublisherStatusChangedEventArgs>(
                TaskCreationOptions.RunContinuationsAsynchronously
            );
            var abortedAfterStart = new TaskCompletionSource<WiFiDirectAdvertisementPublisherStatusChangedEventArgs>(
                TaskCreationOptions.RunContinuationsAsynchronously
            );
            var hasStarted = false;

            publisher.StatusChanged += (_, eventArgs) =>
            {
                if (eventArgs.Status == WiFiDirectAdvertisementPublisherStatus.Started)
                {
                    hasStarted = true;
                    started.TrySetResult(eventArgs);
                }
                else if (eventArgs.Status == WiFiDirectAdvertisementPublisherStatus.Aborted)
                {
                    if (hasStarted)
                    {
                        abortedAfterStart.TrySetResult(eventArgs);
                    }
                    else
                    {
                        started.TrySetResult(eventArgs);
                    }
                }
            };

            publisher.Start();
            var completed = await Task.WhenAny(started.Task, Task.Delay(TimeSpan.FromSeconds(15), cancellation.Token));
            if (completed != started.Task)
            {
                publisher.Stop();
                WriteJson(new
                {
                    status = "error",
                    code = "start_timeout",
                    message = "Wi-Fi Direct 레거시 AP가 15초 안에 시작되지 않았습니다."
                });
                return 3;
            }

            var startResult = await started.Task;
            if (startResult.Status != WiFiDirectAdvertisementPublisherStatus.Started)
            {
                publisher.Stop();
                WriteJson(new
                {
                    status = "error",
                    code = "publisher_aborted",
                    error = startResult.Error.ToString(),
                    message = ExplainError(startResult.Error)
                });
                return 4;
            }

            await Task.Delay(700, cancellation.Token).ContinueWith(_ => { }, TaskScheduler.Default);
            WriteJson(new
            {
                status = "started",
                ssid = options.Ssid,
                addresses = DiscoverSoftApAddresses()
            });

            var stdinStop = Task.Run(async () =>
            {
                while (!cancellation.IsCancellationRequested)
                {
                    var line = await Console.In.ReadLineAsync();
                    if (line is null || line.Trim().Equals("stop", StringComparison.OrdinalIgnoreCase))
                    {
                        cancellation.Cancel();
                        break;
                    }
                }
            });

            var runtimeResult = await Task.WhenAny(
                Task.Delay(Timeout.InfiniteTimeSpan, cancellation.Token),
                abortedAfterStart.Task,
                stdinStop
            );

            if (runtimeResult == abortedAfterStart.Task)
            {
                var aborted = await abortedAfterStart.Task;
                WriteJson(new
                {
                    status = "aborted",
                    error = aborted.Error.ToString(),
                    message = ExplainError(aborted.Error)
                });
            }

            publisher.Stop();
            return 0;
        }
        catch (OperationCanceledException)
        {
            return 0;
        }
        catch (Exception exception)
        {
            WriteJson(new
            {
                status = "error",
                code = "exception",
                message = exception.Message,
                detail = exception.GetType().FullName
            });
            return 1;
        }
    }

    private static Options ParseArguments(string[] args)
    {
        var ssid = string.Empty;
        var password = string.Empty;
        var validateOnly = false;

        for (var index = 0; index < args.Length; index++)
        {
            switch (args[index])
            {
                case "--ssid" when index + 1 < args.Length:
                    ssid = args[++index];
                    break;
                case "--password" when index + 1 < args.Length:
                    password = args[++index];
                    break;
                case "--validate-only":
                    validateOnly = true;
                    break;
                default:
                    throw new ArgumentException($"알 수 없는 인수: {args[index]}");
            }
        }

        return new Options(ssid, password, validateOnly);
    }

    private static void Validate(string ssid, string password)
    {
        var ssidBytes = System.Text.Encoding.UTF8.GetByteCount(ssid);
        if (ssidBytes is < 1 or > 32)
        {
            throw new ArgumentException("SSID는 UTF-8 기준 1~32바이트여야 합니다.");
        }
        if (password.Length is < 8 or > 63)
        {
            throw new ArgumentException("비밀번호는 8~63자여야 합니다.");
        }
    }

    private static string[] DiscoverSoftApAddresses()
    {
        var addresses = new List<string>();

        foreach (var networkInterface in NetworkInterface.GetAllNetworkInterfaces())
        {
            var identity = $"{networkInterface.Name} {networkInterface.Description}".ToLowerInvariant();
            var looksLikeVirtualAp = identity.Contains("wi-fi direct")
                || identity.Contains("wifi direct")
                || identity.Contains("local area connection*")
                || identity.Contains("로컬 영역 연결*");
            if (!looksLikeVirtualAp || networkInterface.OperationalStatus != OperationalStatus.Up)
            {
                continue;
            }

            foreach (var unicast in networkInterface.GetIPProperties().UnicastAddresses)
            {
                var address = unicast.Address;
                if (address.AddressFamily != AddressFamily.InterNetwork || !IsPrivate(address))
                {
                    continue;
                }
                addresses.Add(address.ToString());
            }
        }

        if (!addresses.Contains("192.168.137.1"))
        {
            addresses.Add("192.168.137.1");
        }
        return addresses.Distinct().ToArray();
    }

    private static bool IsPrivate(IPAddress address)
    {
        var bytes = address.GetAddressBytes();
        return bytes[0] == 10
            || (bytes[0] == 172 && bytes[1] is >= 16 and <= 31)
            || (bytes[0] == 192 && bytes[1] == 168);
    }

    private static string ExplainError(WiFiDirectError error) => error switch
    {
        WiFiDirectError.RadioNotAvailable => "Wi-Fi가 꺼져 있거나 무선 어댑터를 사용할 수 없습니다.",
        WiFiDirectError.ResourceInUse => "모바일 핫스팟 또는 다른 Wi-Fi Direct 기능이 이미 무선 어댑터를 사용 중입니다.",
        _ => "이 Wi-Fi 어댑터 또는 드라이버가 인터넷 없는 레거시 SoftAP 시작을 지원하지 않습니다."
    };

    private static void WriteJson(object value)
    {
        Console.WriteLine(JsonSerializer.Serialize(value, JsonOptions));
        Console.Out.Flush();
    }

    private sealed record Options(string Ssid, string Password, bool ValidateOnly);
}
