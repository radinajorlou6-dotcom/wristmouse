package com.radin.wristmouse

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.SystemClock
import android.util.Base64
import android.util.Log

/** Records 16 kHz mono audio and sends it as "a <base64 mu-law>" lines (60 ms each). */
class MicStreamer(private val send: (String) -> Unit, private val onEnd: () -> Unit) {

    companion object {
        const val RATE = 16000
        const val CHUNK = 960
        const val MAX_MS = 30_000L

        fun mulaw(sample: Int): Byte {
            var s = sample
            var sign = 0
            if (s < 0) { s = -s; sign = 0x80 }
            if (s > 32635) s = 32635
            s += 0x84
            var exp = 7
            var mask = 0x4000
            while (exp > 0 && (s and mask) == 0) { exp--; mask = mask shr 1 }
            val mant = (s shr (exp + 3)) and 0x0F
            return (sign or (exp shl 4) or mant).inv().toByte()
        }
    }

    @Volatile private var running = false

    fun start() {
        running = true
        Thread({ run() }, "wm-mic").start()
    }

    fun stop() {
        running = false
    }

    @SuppressLint("MissingPermission")
    private fun run() {
        var rec: AudioRecord? = null
        try {
            val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            rec = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, RATE)
            )
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                send("info Microphone unavailable")
                return
            }
            rec.startRecording()
            val buf = ShortArray(CHUNK)
            val enc = ByteArray(CHUNK)
            val t0 = SystemClock.elapsedRealtime()
            while (running && SystemClock.elapsedRealtime() - t0 < MAX_MS) {
                var off = 0
                while (off < CHUNK && running) {
                    val n = rec.read(buf, off, CHUNK - off)
                    if (n <= 0) break
                    off += n
                }
                if (off <= 0) break
                for (i in 0 until off) enc[i] = mulaw(buf[i].toInt())
                send("a " + Base64.encodeToString(enc, 0, off, Base64.NO_WRAP))
            }
        } catch (e: Exception) {
            Log.e(WristService.TAG, "mic failed", e)
            send("info Microphone error: " + e.javaClass.simpleName)
        } finally {
            try { rec?.stop() } catch (_: Exception) {}
            rec?.release()
            running = false
            onEnd()
        }
    }
}
