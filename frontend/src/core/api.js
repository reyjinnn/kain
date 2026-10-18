import { actions } from './state.js';
import { errors } from '../config/copy.en.js';
export async function api(path, payload = undefined, signal = undefined) {
    const response = await fetch("/api" + path, {
        method: payload === undefined ? "GET" : "POST",
        headers: payload === undefined ? {} : { "Content-Type": "application/json" },
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal,
        cache: "no-store",
    });
    const value = await response.json();
    if (!response.ok) {
        const detail = value.detail || value;
        const error = Object.assign(new Error(errors[detail.code] ||
            "Service could not process the request. Your inputs are safe; please try again."), { detail, status: response.status });
        if (response.status === 409 && detail.code === 'STALE_REVISION') {
            await actions.reportConflict?.().catch(() => {});
        }
        throw error;
    }
    return value;
}
