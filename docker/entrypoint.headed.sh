#!/bin/sh
set -eu

DISPLAY_VALUE="${DISPLAY:-:99}"
XVFB_SCREEN_VALUE="${XVFB_SCREEN:-1440x900x24}"
export DISPLAY="${DISPLAY_VALUE}"

resolve_browser_path() {
python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    print(p.chromium.executable_path or "")
PY
}

if [ -z "${BROWSER_EXECUTABLE_PATH:-}" ] || [ ! -x "${BROWSER_EXECUTABLE_PATH:-}" ]; then
    detected_browser_path="$(resolve_browser_path 2>/dev/null | tr -d '\r' | tail -n 1)"
    if [ -n "${detected_browser_path}" ] && [ -x "${detected_browser_path}" ]; then
        export BROWSER_EXECUTABLE_PATH="${detected_browser_path}"
    fi
fi

if [ "${ALLOW_DOCKER_HEADED_CAPTCHA:-true}" = "true" ] || [ "${ALLOW_DOCKER_HEADED_CAPTCHA:-1}" = "1" ]; then
    display_suffix="$(printf '%s' "${DISPLAY}" | sed 's/^://; s/\..*$//')"
    socket_path="/tmp/.X11-unix/X${display_suffix}"

    mkdir -p /tmp/.X11-unix
    rm -f "/tmp/.X${display_suffix}-lock"

    echo "[entrypoint] starting Xvfb on DISPLAY=${DISPLAY} (${XVFB_SCREEN_VALUE})"
    Xvfb "${DISPLAY}" -screen 0 "${XVFB_SCREEN_VALUE}" -ac +extension RANDR >/tmp/xvfb.log 2>&1 &

    waited=0
    while [ ! -S "${socket_path}" ] && [ "${waited}" -lt 100 ]; do
        sleep 0.1
        waited=$((waited + 1))
    done

    if [ ! -S "${socket_path}" ]; then
        echo "[entrypoint] failed to start Xvfb, socket not ready: ${socket_path}" >&2
        exit 1
    fi

    echo "[entrypoint] starting Fluxbox on DISPLAY=${DISPLAY}"
    fluxbox >/tmp/fluxbox.log 2>&1 &

    # Optional VNC server so you can view :99 with RealVNC/TigerVNC (localhost:5900 by default).
    # Set ENABLE_HEADED_VNC=0 to disable. Set VNC_PASSWORD for auth; otherwise -nopw (dev only).
    case "${ENABLE_HEADED_VNC:-1}" in
        0|false|FALSE|no|NO|off|OFF)
            echo "[entrypoint] headed VNC disabled (ENABLE_HEADED_VNC=${ENABLE_HEADED_VNC:-})"
            ;;
        *)
            VNC_PORT_VALUE="${VNC_PORT:-5900}"
            if [ -n "${VNC_PASSWORD:-}" ]; then
                rm -f /tmp/x11vnc.pass
                if x11vnc -storepasswd "${VNC_PASSWORD}" /tmp/x11vnc.pass 2>/dev/null; then
                    VNC_AUTH_OPTS="-rfbauth /tmp/x11vnc.pass"
                else
                    echo "[entrypoint] VNC_PASSWORD set but -storepasswd failed; falling back to -nopw" >&2
                    VNC_AUTH_OPTS="-nopw"
                fi
            else
                VNC_AUTH_OPTS="-nopw"
                echo "[entrypoint] VNC: no VNC_PASSWORD set (passwordless). Do not expose port 5900 to the internet." >&2
            fi
            echo "[entrypoint] starting x11vnc on 0.0.0.0:${VNC_PORT_VALUE} for DISPLAY=${DISPLAY}"
            x11vnc -display "${DISPLAY}" -forever -shared -noxdamage \
                ${VNC_AUTH_OPTS} \
                -listen 0.0.0.0 -rfbport "${VNC_PORT_VALUE}" \
                >/tmp/x11vnc.log 2>&1 &
            ;;
    esac
fi

echo "[entrypoint] starting flow2api (headed browser mode)"
if [ -n "${BROWSER_EXECUTABLE_PATH:-}" ] && [ -x "${BROWSER_EXECUTABLE_PATH}" ]; then
    echo "[entrypoint] browser executable: ${BROWSER_EXECUTABLE_PATH}"
    "${BROWSER_EXECUTABLE_PATH}" --version || true
else
    echo "[entrypoint] warning: no valid browser executable found for personal/browser captcha" >&2
fi

exec python main.py
