package com.radin.wristmouse

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.os.SystemClock
import android.util.Log
import java.io.ByteArrayOutputStream
import java.util.UUID

/**
 * Bluetooth LE link. The watch is a GATT server that advertises our service; the laptop finds it
 * and connects (no pairing needed). Watch -> laptop data goes out as notifications on TX, laptop
 * -> watch commands arrive as writes on RX. Both carry plain text lines.
 */
@SuppressLint("MissingPermission")
class BleLink(
    private val ctx: Context,
    private val onLine: (String) -> Unit,
    private val onConnected: () -> Unit,
) {
    companion object {
        val SERVICE: UUID = UUID.fromString("6e7a0001-5a3c-4d1b-9b8e-77a1c0de1e55")
        val TX: UUID = UUID.fromString("6e7a0002-5a3c-4d1b-9b8e-77a1c0de1e55")
        val RX: UUID = UUID.fromString("6e7a0003-5a3c-4d1b-9b8e-77a1c0de1e55")
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
        const val MAX_QUEUED = 48_000
    }

    private val bm = ctx.getSystemService(BluetoothManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private var server: BluetoothGattServer? = null
    private var txChar: BluetoothGattCharacteristic? = null
    private var advertising = false
    @Volatile private var running = false
    @Volatile private var device: BluetoothDevice? = null
    @Volatile private var subscribed = false
    @Volatile private var mtu = 23

    private val lock = Any()
    private val q = ArrayDeque<ByteArray>()
    private var headOff = 0
    private var queued = 0
    private var inFlight = false
    private var inFlightSince = 0L
    private var retry: ByteArray? = null
    private val rx = StringBuilder()

    fun isUp(): Boolean = device != null && subscribed

    private fun hasPerms(): Boolean {
        if (Build.VERSION.SDK_INT < 31) return true
        return ctx.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED &&
            ctx.checkSelfPermission(Manifest.permission.BLUETOOTH_ADVERTISE) == PackageManager.PERMISSION_GRANTED
    }

    fun start() {
        running = true
        trySetup()
    }

    fun stop() {
        running = false
        main.removeCallbacksAndMessages(null)
        try {
            if (advertising) bm.adapter?.bluetoothLeAdvertiser?.stopAdvertising(advCallback)
        } catch (_: Exception) {}
        advertising = false
        try { server?.close() } catch (_: Exception) {}
        server = null
        device = null
        subscribed = false
    }

    /** Retries every few seconds until Bluetooth is on and permissions are granted. */
    private fun trySetup() {
        if (!running || server != null) return
        val adapter = bm?.adapter
        when {
            !hasPerms() -> Status.ble = "Bluetooth: permission needed (open app)"
            adapter == null -> { Status.ble = "Bluetooth: not available"; return }
            !adapter.isEnabled -> Status.ble = "Bluetooth: turned off"
            else -> {
                try {
                    openServer()
                    return
                } catch (e: Exception) {
                    Log.e(WristService.TAG, "gatt server failed", e)
                    Status.ble = "Bluetooth: error " + e.javaClass.simpleName
                }
            }
        }
        main.postDelayed({ trySetup() }, 5000)
    }

    private fun openServer() {
        val s = bm.openGattServer(ctx, callback) ?: throw IllegalStateException("no gatt server")
        server = s
        val svc = BluetoothGattService(SERVICE, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        val tx = BluetoothGattCharacteristic(
            TX,
            BluetoothGattCharacteristic.PROPERTY_NOTIFY or BluetoothGattCharacteristic.PROPERTY_READ,
            BluetoothGattCharacteristic.PERMISSION_READ,
        )
        tx.addDescriptor(
            BluetoothGattDescriptor(
                CCCD, BluetoothGattDescriptor.PERMISSION_READ or BluetoothGattDescriptor.PERMISSION_WRITE
            )
        )
        val rxc = BluetoothGattCharacteristic(
            RX,
            BluetoothGattCharacteristic.PROPERTY_WRITE or BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE,
            BluetoothGattCharacteristic.PERMISSION_WRITE,
        )
        svc.addCharacteristic(tx)
        svc.addCharacteristic(rxc)
        txChar = tx
        s.addService(svc) // advertising starts in onServiceAdded
        Status.ble = "Bluetooth: starting…"
    }

    private val advCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings?) {
            advertising = true
            if (device == null) Status.ble = "Bluetooth: waiting for laptop…"
        }

        override fun onStartFailure(errorCode: Int) {
            advertising = false
            Status.ble = "Bluetooth: advertising failed ($errorCode)"
            if (running) main.postDelayed({ startAdvertising() }, 5000)
        }
    }

    private fun startAdvertising() {
        if (!running || advertising) return
        val adv = bm.adapter?.bluetoothLeAdvertiser
        if (adv == null) {
            Status.ble = "Bluetooth: this watch can't advertise"
            return
        }
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_MEDIUM)
            .setConnectable(true)
            .setTimeout(0)
            .build()
        val data = AdvertiseData.Builder()
            .setIncludeDeviceName(false)
            .addServiceUuid(ParcelUuid(SERVICE))
            .build()
        try {
            adv.startAdvertising(settings, data, advCallback)
        } catch (e: Exception) {
            Status.ble = "Bluetooth: advertising error"
            Log.e(WristService.TAG, "advertise", e)
        }
    }

    private fun stopAdvertising() {
        if (!advertising) return
        try { bm.adapter?.bluetoothLeAdvertiser?.stopAdvertising(advCallback) } catch (_: Exception) {}
        advertising = false
    }

    private val callback = object : BluetoothGattServerCallback() {
        override fun onServiceAdded(status: Int, service: BluetoothGattService?) {
            main.post { startAdvertising() }
        }

        override fun onConnectionStateChange(dev: BluetoothDevice, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                // Don't clear `subscribed` here: this callback can arrive after the laptop's
                // subscribe request on a quick reconnect, which left the watch silent.
                if (device?.address != dev.address) subscribed = false
                device = dev
                mtu = 23
                Status.ble = "Bluetooth: connecting…"
                main.post { stopAdvertising() }
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                if (device == null || device?.address == dev.address) {
                    device = null
                    subscribed = false
                    synchronized(lock) { q.clear(); headOff = 0; queued = 0; inFlight = false; retry = null }
                    Status.ble = "Bluetooth: disconnected"
                    main.post { startAdvertising() }
                }
            }
        }

        override fun onMtuChanged(dev: BluetoothDevice, newMtu: Int) {
            mtu = newMtu
        }

        override fun onDescriptorWriteRequest(
            dev: BluetoothDevice, requestId: Int, descriptor: BluetoothGattDescriptor,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray?,
        ) {
            if (responseNeeded) server?.sendResponse(dev, requestId, BluetoothGatt.GATT_SUCCESS, 0, null)
            if (descriptor.uuid == CCCD) {
                val on = value != null && value.isNotEmpty() && (value[0].toInt() and 0x01) != 0
                device = dev
                subscribed = on
                if (on) {
                    Status.ble = "Bluetooth: connected"
                    onConnected()
                }
            }
        }

        override fun onDescriptorReadRequest(
            dev: BluetoothDevice, requestId: Int, offset: Int, descriptor: BluetoothGattDescriptor,
        ) {
            val v = if (subscribed) BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            else BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE
            server?.sendResponse(dev, requestId, BluetoothGatt.GATT_SUCCESS, 0, v)
        }

        override fun onCharacteristicReadRequest(
            dev: BluetoothDevice, requestId: Int, offset: Int, characteristic: BluetoothGattCharacteristic,
        ) {
            server?.sendResponse(dev, requestId, BluetoothGatt.GATT_SUCCESS, 0, ByteArray(0))
        }

        override fun onCharacteristicWriteRequest(
            dev: BluetoothDevice, requestId: Int, characteristic: BluetoothGattCharacteristic,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray?,
        ) {
            if (responseNeeded) server?.sendResponse(dev, requestId, BluetoothGatt.GATT_SUCCESS, 0, null)
            if (characteristic.uuid != RX || value == null) return
            val lines = ArrayList<String>()
            synchronized(rx) {
                rx.append(String(value, Charsets.UTF_8))
                while (true) {
                    val i = rx.indexOf("\n")
                    if (i < 0) break
                    lines.add(rx.substring(0, i).trimEnd('\r'))
                    rx.delete(0, i + 1)
                }
                if (rx.length > 8192) rx.setLength(0)
            }
            for (l in lines) {
                if (l == "HELLO-PC") {
                    // the laptop only sends this after subscribing, so we know it's listening
                    device = dev
                    if (!subscribed) {
                        subscribed = true
                        Status.ble = "Bluetooth: connected"
                    }
                    onConnected()
                    pump()
                } else {
                    onLine(l)
                }
            }
        }

        override fun onNotificationSent(dev: BluetoothDevice, status: Int) {
            synchronized(lock) { inFlight = false }
            pump()
        }
    }

    fun send(line: String) {
        val bytes = (line + "\n").toByteArray(Charsets.UTF_8)
        synchronized(lock) {
            if (queued + bytes.size > MAX_QUEUED && line.startsWith("s ")) return // drop motion, never audio
            q.addLast(bytes)
            queued += bytes.size
        }
        pump()
    }

    private fun takeChunk(max: Int): ByteArray? {
        if (q.isEmpty()) return null
        val out = ByteArrayOutputStream(max)
        while (q.isNotEmpty() && out.size() < max) {
            val h = q.first()
            val n = minOf(h.size - headOff, max - out.size())
            out.write(h, headOff, n)
            headOff += n
            if (headOff >= h.size) {
                q.removeFirst()
                headOff = 0
            }
        }
        queued -= out.size()
        return out.toByteArray()
    }

    @Suppress("DEPRECATION")
    private fun pump() {
        val dev = device ?: return
        val s = server ?: return
        val ch = txChar ?: return
        if (!subscribed) return
        val chunk: ByteArray = synchronized(lock) {
            val now = SystemClock.elapsedRealtime()
            if (inFlight && now - inFlightSince < 1000) return
            val r = retry
            retry = null
            val c = r ?: takeChunk(maxOf(20, minOf(mtu - 3, 509))) ?: return
            inFlight = true
            inFlightSince = now
            c
        }
        val ok = try {
            if (Build.VERSION.SDK_INT >= 33) {
                s.notifyCharacteristicChanged(dev, ch, false, chunk) == BluetoothStatusCodes.SUCCESS
            } else {
                ch.value = chunk
                s.notifyCharacteristicChanged(dev, ch, false)
            }
        } catch (e: Exception) {
            false
        }
        if (!ok) {
            synchronized(lock) { inFlight = false; retry = chunk }
            main.postDelayed({ pump() }, 15)
        }
    }
}
