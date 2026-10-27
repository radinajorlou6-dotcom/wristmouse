package com.radin.wristmouse

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.HandlerThread

/**
 * Streams accelerometer + gyroscope at ~100 Hz as
 * "s <t_ms> ax ay az gx gy gz" (accel in mm/s^2, gyro in mrad/s).
 */
class SensorStreamer(
    ctx: Context,
    private val send: (String) -> Unit,
    private val linkUp: () -> Boolean,
) : SensorEventListener {

    private val sm = ctx.getSystemService(SensorManager::class.java)
    private var thread: HandlerThread? = null
    private val acc = FloatArray(3)
    private var hasAcc = false
    private val sb = StringBuilder(64)

    @Volatile var paused = false

    fun start() {
        val t = HandlerThread("wm-sensors").also { it.start() }
        thread = t
        val h = Handler(t.looper)
        sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sm.registerListener(this, it, 10_000, 0, h) }
        sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let { sm.registerListener(this, it, 10_000, 0, h) }
    }

    fun stop() {
        sm.unregisterListener(this)
        thread?.quitSafely()
        thread = null
    }

    override fun onSensorChanged(e: SensorEvent) {
        when (e.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                acc[0] = e.values[0]; acc[1] = e.values[1]; acc[2] = e.values[2]
                hasAcc = true
            }
            Sensor.TYPE_GYROSCOPE -> {
                if (!hasAcc || paused || !linkUp()) return
                sb.setLength(0)
                sb.append("s ").append(e.timestamp / 1_000_000L)
                for (v in acc) sb.append(' ').append((v * 1000f).toInt())
                for (i in 0 until 3) sb.append(' ').append((e.values[i] * 1000f).toInt())
                send(sb.toString())
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
}
