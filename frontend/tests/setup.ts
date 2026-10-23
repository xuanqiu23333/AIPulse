import { afterEach, beforeEach, vi } from 'vitest'
beforeEach(() => {
 vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('Unmocked HTTP is blocked in offline tests'))))
 Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = '' })
HTMLDialogElement.prototype.showModal = function () { this.open = true }
HTMLDialogElement.prototype.close = function () { this.open = false }
