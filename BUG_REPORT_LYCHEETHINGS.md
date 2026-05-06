# Bug Report: `GET /devices` returns 500 with `TypeError: Cannot read properties of undefined (reading 'length')`

## Summary

The public SmartSlydr REST API endpoint `GET /devices` is returning an unhandled
exception from the Lambda handler. The handler crashes before producing a
device list, so any third-party integration (Home Assistant, etc.) cannot fetch
devices for the affected account.

The integration has been working without changes for several months and stopped
returning devices on **2026-05-05** (US Central). No client-side change occurred
between the last successful call and the first failing call.

## Affected Account

- **Email**: _(fill in the account email used for `LycheeThings`)_
- **Approximate first failure**: 2026-05-05 around 23:08 local time (UTC-5)

## Request

Per `SmartSlydr REST API` documentation v0.4 (Device List section):

```
GET https://34yl6ald82.execute-api.us-east-2.amazonaws.com/prod/devices
Headers:
    Authorization: <access_token>     # access_token returned by /auth, no body
```

The same auth flow that returns this token is succeeding (auth is not the
problem — the `Authorization` header is accepted, the request reaches the
handler, and the handler throws partway through).

## Response (actual, broken)

Instead of `{ "statusCode": 200, "room_lists": [...] }`, the API returns:

```json
{
    "errorType": "TypeError",
    "errorMessage": "Cannot read properties of undefined (reading 'length')",
    "trace": [
        "TypeError: Cannot read properties of undefined (reading 'length')",
        "    at Runtime.exports.handler (/var/task/index.js:143:38)",
        "    at runMicrotasks (<anonymous>)",
        "    at processTicksAndRejections (node:internal/process/task_queues:96:5)"
    ]
}
```

## Expected Response

Per the API doc:

```json
{
    "statusCode": 200,
    "room_lists": [ { "room_name": "...", "device_list": [ ... ] }, ... ]
}
```

## Likely Root Cause (best-effort guess)

The Lambda handler at `/var/task/index.js:143:38` is reading `.length` on a
value that is `undefined`. The most likely candidates are:

1. The user's account profile no longer contains the array the handler
   iterates (e.g. `room_lists`, a `device_list` array, a `petpass` array, or
   a similar collection field) — perhaps a backend data-shape change wasn't
   migrated for older accounts.
2. A device or room record is missing a required nested array, and the
   handler iterates over it without a guard like `(value || []).length` or
   `Array.isArray(value)`.

A defensive fix in the handler that defaults missing arrays to `[]` would
both prevent the 500 *and* unblock affected accounts immediately, even before
the underlying data shape is investigated.

## How This Was Diagnosed

The Home Assistant `Lychee Things` integration calls the documented endpoints
exactly as specified in `SmartSlydr_REST_API_0_4.pdf`. The integration
correctly detects that the response is missing `room_lists` and surfaces the
backend's error JSON in its logs (`custom_components.smartslydr.api_client:
Unexpected /devices response: { 'errorType': 'TypeError', ... }`). That log is
how the trace above was captured.

Auth (`POST /auth`) and `/operation/get`, `/operation` were not separately
exercised once `/devices` started failing, but auth is clearly succeeding (the
handler is being reached with a valid token).

## What Would Help Confirm a Fix

Once a fix is deployed, a `GET /devices` for the affected account should
return the documented `room_lists` shape. The Home Assistant integration will
recover automatically on its next polling cycle (no client action needed).

## Contact

_(fill in your preferred contact channel for follow-up)_
