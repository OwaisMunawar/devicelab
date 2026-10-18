# Hardware validation

What has been run on physical iPhones, what has not, and the checklist for doing it on your
own lab.

## Status

| Check | Result |
| --- | --- |
| `UsbDeviceProvider` inventory and health polling (model, iOS version, battery, free storage, connection state) on one physical iPhone 15 (iOS 26.6.1), read-only | **Verified** |
| Control plane to host agent to `UsbDeviceProvider` to Appium 3 / WebDriverAgent on that iPhone: launch Calculator, tap 7 + 8 =, assert 15, screenshot ([`examples/hardware/calculator-check.yaml`](../examples/hardware/calculator-check.yaml)) | **Verified**, one run, 3 steps succeeded. Screenshot: [`screenshots/physical-iphone-calculator-result.png`](screenshots/physical-iphone-calculator-result.png) |
| `devicectl` install, `idevicesyslog` log capture, `idevicediagnostics restart`, appearance, locale and time zone changes on a physical device | **Not run.** Code and unit tests exist; log capture and settings were deliberately not exercised on a personal phone. |
| More than one physical device, Wi-Fi (network) devices, iOS 16 and earlier, iPad | **Not run** |
| Thermal readings from a physical device | **Not available**: libimobiledevice does not expose them, so USB devices always report `nominal` |

The physical run was limited to the built-in Calculator app on a single device, with
WebDriverAgent installed for the run and removed afterwards.

## Findings from the first physical run

- With automatic signing, the `CODE_SIGN_IDENTITY` must be the generic `Apple Development`
  plus `DEVELOPMENT_TEAM`; naming a specific certificate makes Xcode refuse ("conflicting
  provisioning settings"). The agent reads `DEVICELAB_TEAM_ID` and `DEVICELAB_SIGNING_ID`
  from the environment, never from the repo.
- Appium 3 needs Node 22.12+ or 24; odd-numbered Node releases are rejected.
- The first UI step on a cold cache builds and installs WebDriverAgent, which takes minutes.
  Give that step a large `timeout_s`, as the example template does.
- The phone must be unlocked and awake while WebDriverAgent starts; a locked device fails the
  launch with a generic `xcodebuild` exit 65.
- iOS 26 Calculator keys are addressable by name (`Seven`, `Add`, `Eight`, `Equals`) and the
  result is a static text; a predicate locator finds it.

## Checklist for your own lab

Prerequisites: Xcode, the device paired and trusted, Developer Mode on, an Apple Development
signing identity for a team you control.

1. **Inventory (read-only).** `idevice_id -l` lists the device. Start an agent with
   `--provider usb --usb-allow <udid>` and confirm the dashboard shows model, iOS version,
   battery and storage within a minute. Unplug it: it should go offline within the
   heartbeat timeout; replug and it returns to available.
2. **Signing.** `export DEVICELAB_TEAM_ID=...` (and `DEVICELAB_SIGNING_ID` if you need to).
   Build once by hand to see any signing error early:
   `xcodebuild build-for-testing -project .../WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner -destination id=<udid> -allowProvisioningUpdates DEVELOPMENT_TEAM=$DEVICELAB_TEAM_ID`.
3. **First automation run.** Keep the phone unlocked. Accept any Trust or keychain prompts; if
   WebDriverAgentRunner shows "Untrusted Developer", trust your certificate under
   Settings, General, VPN & Device Management.
4. **Smoke workflow.** Submit `calculator-check`, or a template using an app you own.
5. **Failure handling.** Pull the cable mid-run: the lease must expire and the job requeue on
   another device (needs a second device). Lock the screen mid-run and confirm the failure is
   classified and retried.
6. **Cleanup.** Uninstall the runner: `xcrun devicectl device uninstall app --device <udid>
   <your wda bundle id>.xctrunner`.

## Privacy notes

Agents report the device name; use `--redact-names` to publish a generic name instead.
Never put a real UDID, serial or Apple ID in a template, an issue or a screenshot. Logs and
screenshots can contain personal data; do not run collection steps on a phone you use.
