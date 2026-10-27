package com.radin.wristmouse

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log

/** Shared status for the screen. Plain volatile fields; the UI just polls them. */
object Status {
    @Volatile var wifi = "Wi-Fi: starting"
    @Volatile var ble = "Bluetooth: starting"
    @Volatile var mouseOn: Boolean? = null
    @Volatile var message = ""
    @Volatile var running = false
    @Volatile var micReady = false
    @Volatile var listening = false
}

/**
 * Runs in the background with the screen off. Streams motion sensors and (on request) the mic to
 * the laptop over Wi-Fi (preferred) or Bluetooth LE, and vibrates when the laptop asks it to.
 */
class WristService : Service() {

    companion object {
        const val TAG = "WristMouse"
        const val EXTRA_MIC = "mic"
        const val ACTION_STOP = "com.radin.wristmouse.STOP"
        private const val CHANNEL = "wristmouse"
        private const val NOTIF_ID = 1

        fun start(ctx: Context, withMic: Boolean) {
            val i = Intent(ctx, WristService::class.java).putExtra(EXTRA_MIC, withMic)
            ctx.startForegroundService(i)
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, WristService::class.java).setAction(ACTION_STOP))
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private var started = false
    private var withMic = false
    private var wakeLock: PowerManager.WakeLock? = null
    private lateinit var wifi: WifiLink
    private lateinit var ble: BleLink
    private lateinit var sensors: SensorStreamer
    private var mic: MicStreamer? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        val wantMic = intent?.getBooleanExtra(EXTRA_MIC, false) ?: false
        goForeground(wantMic || withMic)
        if (!started) {
            started = true
            startAll()
        }
        return START_STICKY
    }

    private fun goForeground(wantMic: Boolean) {
        val micOk = wantMic && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        val n = buildNotification()
        try {
            var types = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
            if (micOk) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            startForeground(NOTIF_ID, n, types)
            withMic = micOk
        } catch (e: Exception) {
            Log.w(TAG, "foreground with mic failed, retrying without", e)
            try {
                startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            } catch (e2: Exception) {
                Log.e(TAG, "foreground failed", e2)
            }
            withMic = false
        }
        Status.micReady = withMic
    }

    private fun buildNotification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "WristMouse", NotificationManager.IMPORTANCE_LOW))
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("WristMouse")
            .setContentText("Controlling your laptop")
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    private fun startAll() {
        Status.running = true
        val pm = getSystemService(PowerManager::class.java)
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "WristMouse:stream").apply {
            setReferenceCounted(false)
            acquire()
        }
        wifi = WifiLink(this, ::onLine, ::onLinkUp)
        ble = BleLink(this, ::onLine, ::onLinkUp)
        sensors = SensorStreamer(this, ::send) { wifi.isUp() || ble.isUp() }
        wifi.start()
        ble.start()
        sensors.start()
    }

    override fun onDestroy() {
        Status.running = false
        mic?.stop()
        if (started) {
            sensors.stop()
            wifi.stop()
            ble.stop()
        }
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    /** Send to the laptop: Wi-Fi when connected, otherwise Bluetooth. */
    fun send(line: String) {
        if (wifi.isUp()) wifi.send(line) else if (ble.isUp()) ble.send(line)
    }

    private fun onLinkUp() {
        send("HELLO 1 " + Build.MANUFACTURER + " " + Build.MODEL)
    }

    private fun onLine(line: String) {
        main.post { handle(line) }
    }

    private fun handle(line: String) {
        when {
            line == "p" -> {}
            line == "mic 1" -> startMic()
            line == "mic 0" -> stopMic()
            line.startsWith("vib ") -> vibrate(line.substring(4))
            line.startsWith("msg ") -> Status.message = line.substring(4)
            line.startsWith("state ") -> Status.mouseOn = line.substring(6).trim() == "on"
        }
    }

    private fun startMic() {
        if (mic != null) return
        if (!withMic) {
            send("info Voice needs the WristMouse app opened once on the watch")
            send("ae")
            Status.message = "Open this app to enable voice"
            return
        }
        sensors.paused = true
        Status.listening = true
        val m = MicStreamer(::send) {
            main.post {
                mic = null
                sensors.paused = false
                Status.listening = false
            }
            send("ae")
        }
        mic = m
        m.start()
    }

    private fun stopMic() {
        mic?.stop()
    }

    private fun vibrate(spec: String) {
        val parts = spec.split(',').mapNotNull { it.trim().toLongOrNull() }.take(8)
        if (parts.isEmpty()) return
        val timings = LongArray(parts.size + 1)
        parts.forEachIndexed { i, v -> timings[i + 1] = v.coerceIn(1L, 1000L) }
        try {
            val v: Vibrator = if (Build.VERSION.SDK_INT >= 31) {
                getSystemService(VibratorManager::class.java).defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                getSystemService(Vibrator::class.java)
            }
            v.vibrate(VibrationEffect.createWaveform(timings, -1))
        } catch (e: Exception) {
            Log.w(TAG, "vibrate failed", e)
        }
    }
}
