package com.radin.wristmouse

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/** Starts the mouse after the watch restarts (voice needs the app opened once). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        try {
            WristService.start(context, withMic = false)
        } catch (e: Exception) {
            Log.w(WristService.TAG, "boot start failed", e)
        }
    }
}
