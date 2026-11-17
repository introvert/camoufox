"""
Fingerprint preset generation, injection, and profile config conversion.
"""

import json
import re
import sys

from constants import WEBRTC_TEST_IP


# ─── Preset Generation ────────────────────────────────────────────────────────

def convert_preset(ctx: dict) -> dict:
    """Convert generate_context_fingerprint() result to camelCase dict."""
    config = ctx["config"]

    return {
        "initScript": ctx["init_script"],
        "contextOptions": {
            "userAgent": ctx["context_options"].get("user_agent"),
            "viewport": ctx["context_options"].get("viewport"),
            "deviceScaleFactor": ctx["context_options"].get("device_scale_factor"),
            "locale": ctx["context_options"].get("locale"),
            "timezoneId": ctx["context_options"].get("timezone_id"),
        },
        "camouConfig": config,
        "profileConfig": {
            "screenWidth": config["screen.width"],
            "screenHeight": config["screen.height"],
            "screenColorDepth": config["screen.colorDepth"],
            "navigatorPlatform": config["navigator.platform"],
            "navigatorOscpu": config["navigator.oscpu"],
            "navigatorUserAgent": config["navigator.userAgent"],
            "hardwareConcurrency": config["navigator.hardwareConcurrency"],
            "webglVendor": config["webGl:vendor"],
            "webglRenderer": config["webGl:renderer"],
            "timezone": config.get("timezone", ""),
            "fontList": config["fonts"],
            "speechVoices": config["voices"],
        },
    }


def generate_presets(ff_version: str) -> dict:
    """Identities for the binary under test, claiming its Firefox version as the
    launchers do (NewContext defaults ff_version to the browser's own)."""
    try:
        from camoufox.fingerprints import generate_context_fingerprint
    except ImportError:
        print(
            "ERROR: camoufox Python package not installed.\n"
            "  Use the wrapper: ./run_tests.sh <binary_path>\n"
            "  Or install manually: pip install -e ../../../python",
            file=sys.stderr,
        )
        sys.exit(1)

    print("  Generating 3 macOS per-context profiles...")
    mac_per_context = [convert_preset(generate_context_fingerprint(os="macos", ff_version=ff_version)) for _ in range(3)]
    print("  Generating 3 Linux per-context profiles...")
    linux_per_context = [convert_preset(generate_context_fingerprint(os="linux", ff_version=ff_version)) for _ in range(3)]
    print("  Generating macOS global profile...")
    mac_global = convert_preset(generate_context_fingerprint(os="macos", ff_version=ff_version))
    print("  Generating Linux global profile...")
    linux_global = convert_preset(generate_context_fingerprint(os="linux", ff_version=ff_version))

    return {
        "macPerContext": mac_per_context,
        "linuxPerContext": linux_per_context,
        "macGlobal": mac_global,
        "linuxGlobal": linux_global,
    }


# ─── Preset Injection ─────────────────────────────────────────────────────────

def inject_timezone(preset: dict, timezone: str) -> None:
    preset["initScript"] = re.sub(
        r"w\.setTimezone\(Intl\.DateTimeFormat\(\)\.resolvedOptions\(\)\.timeZone\)",
        f"w.setTimezone({json.dumps(timezone)})",
        preset["initScript"],
    )
    preset["contextOptions"]["timezoneId"] = timezone
    preset["profileConfig"]["timezone"] = timezone
    preset["camouConfig"]["timezone"] = timezone


def inject_webrtc_ip(preset: dict) -> None:
    preset["initScript"] = re.sub(
        r'w\.setWebRTCIPv4\(""\)',
        f"w.setWebRTCIPv4({json.dumps(WEBRTC_TEST_IP)})",
        preset["initScript"],
    )


# ─── Profile Config ───────────────────────────────────────────────────────────

def preset_to_profile_config(preset: dict, name: str, os_type: str, mode: str) -> dict:
    pc = preset["profileConfig"]
    return {
        "name": name,
        "os": os_type,
        "mode": mode,
        "platform": pc["navigatorPlatform"],
        "oscpu": pc["navigatorOscpu"],
        "userAgent": pc["navigatorUserAgent"],
        "hardwareConcurrency": pc["hardwareConcurrency"],
        "screenWidth": pc["screenWidth"],
        "screenHeight": pc["screenHeight"],
        "colorDepth": pc["screenColorDepth"],
        "timezone": pc["timezone"],
        "webglVendor": pc["webglVendor"],
        "webglRenderer": pc["webglRenderer"],
    }
