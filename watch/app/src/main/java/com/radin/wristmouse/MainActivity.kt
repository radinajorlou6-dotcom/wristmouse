package com.radin.wristmouse

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * The only screen. Opening it starts the background service (with voice enabled); after that you
 * can leave the app and never touch the watch again.
 */
class MainActivity : Activity() {

    private val handler = Handler(Looper.getMainLooper())
    private lateinit var title: TextView
    private lateinit var status: TextView
    private lateinit var message: TextView
    private lateinit var button: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (resources.displayMetrics.widthPixels * 0.14f).toInt()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(pad, pad / 2, pad, pad)
        }
        title = TextView(this).apply {
            text = "WristMouse"
            textSize = 18f
            setTypeface(typeface, Typeface.BOLD)
            gravity = Gravity.CENTER
        }
        status = TextView(this).apply {
            textSize = 12f
            gravity = Gravity.CENTER
            setPadding(0, 8, 0, 8)
        }
        message = TextView(this).apply {
            textSize = 12f
            gravity = Gravity.CENTER
            setTextColor(Color.rgb(140, 200, 255))
        }
        button = Button(this).apply {
            setOnClickListener {
                if (Status.running) WristService.stop(this@MainActivity) else startOrAsk()
            }
        }
        col.addView(title)
        col.addView(status)
        col.addView(message)
        col.addView(button)
        setContentView(ScrollView(this).apply { addView(col); isVerticalScrollBarEnabled = false })
        startOrAsk()
    }

    private fun neededPermissions(): Array<String> {
        val list = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 31) {
            list += Manifest.permission.BLUETOOTH_ADVERTISE
            list += Manifest.permission.BLUETOOTH_CONNECT
        }
        if (Build.VERSION.SDK_INT >= 33) list += Manifest.permission.POST_NOTIFICATIONS
        return list.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }.toTypedArray()
    }

    private fun startOrAsk() {
        val missing = neededPermissions()
        if (missing.isNotEmpty()) requestPermissions(missing, 1) else WristService.start(this, withMic = true)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        WristService.start(this, withMic = true)
    }

    private val refresh = object : Runnable {
        override fun run() {
            val mouse = when (Status.mouseOn) {
                true -> "Mouse: ON"
                false -> "Mouse: PAUSED"
                null -> "Mouse: –"
            }
            val lines = mutableListOf(Status.wifi, Status.ble, mouse)
            if (Status.listening) lines += "🎤 Listening…"
            else if (!Status.micReady && Status.running) lines += "Voice: reopen app to enable"
            status.text = if (Status.running) lines.joinToString("\n") else "Stopped"
            message.text = Status.message
            message.visibility = if (Status.message.isEmpty()) View.GONE else View.VISIBLE
            button.text = if (Status.running) "Stop" else "Start"
            handler.postDelayed(this, 500)
        }
    }

    override fun onResume() {
        super.onResume()
        handler.post(refresh)
    }

    override fun onPause() {
        handler.removeCallbacks(refresh)
        super.onPause()
    }
}
