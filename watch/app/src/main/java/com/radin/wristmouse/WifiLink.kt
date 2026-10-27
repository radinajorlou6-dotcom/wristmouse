package com.radin.wristmouse

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiManager
import android.util.Log
import java.io.BufferedOutputStream
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Wi-Fi link. Wear OS normally keeps Wi-Fi off while Bluetooth to the phone is connected, so we
 * explicitly request a Wi-Fi network. The laptop broadcasts "WRISTMOUSE <port> <name>" on UDP
 * 47321 every second; we connect to whoever sends it.
 */
class WifiLink(
    private val ctx: Context,
    private val onLine: (String) -> Unit,
    private val onConnected: () -> Unit,
) {
    companion object {
        const val UDP_PORT = 47321
    }

    private val cm = ctx.getSystemService(ConnectivityManager::class.java)
    private val prefs = ctx.getSharedPreferences("wifi", Context.MODE_PRIVATE)
    @Volatile private var network: Network? = null
    @Volatile private var socket: Socket? = null
    @Volatile private var running = false
    private val outQ = LinkedBlockingQueue<String>(4000)
    private var multicastLock: WifiManager.MulticastLock? = null

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(n: Network) {
            network = n
        }

        override fun onLost(n: Network) {
            if (network == n) {
                network = null
                closeSocket()
            }
        }
    }

    fun isUp(): Boolean = socket?.let { it.isConnected && !it.isClosed } ?: false

    fun send(line: String) {
        if (isUp()) outQ.offer(line)
    }

    fun start() {
        running = true
        try {
            val req = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build()
            cm.requestNetwork(req, callback)
        } catch (e: Exception) {
            Log.e(WristService.TAG, "requestNetwork failed", e)
            Status.wifi = "Wi-Fi: unavailable"
        }
        try {
            val wm = ctx.applicationContext.getSystemService(WifiManager::class.java)
            multicastLock = wm.createMulticastLock("wristmouse").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
            Log.w(WristService.TAG, "multicast lock failed", e)
        }
        Thread({ loop() }, "wm-wifi").start()
    }

    fun stop() {
        running = false
        closeSocket()
        try { cm.unregisterNetworkCallback(callback) } catch (_: Exception) {}
        multicastLock?.let { if (it.isHeld) it.release() }
    }

    private fun loop() {
        while (running) {
            try {
                val net = network
                if (net == null) {
                    Status.wifi = "Wi-Fi: waiting for Wi-Fi…"
                    Thread.sleep(1000)
                    continue
                }
                Status.wifi = "Wi-Fi: looking for laptop…"
                val target = findLaptop(net)
                if (target == null) {
                    Thread.sleep(500)
                    continue
                }
                connect(net, target.first, target.second, target.third)
            } catch (e: InterruptedException) {
                return
            } catch (e: Exception) {
                Log.w(WristService.TAG, "wifi loop", e)
            }
            try { Thread.sleep(1000) } catch (_: InterruptedException) { return }
        }
    }

    /** Wait up to 3 s for a beacon; fall back to the last laptop we connected to. */
    private fun findLaptop(net: Network): Triple<String, Int, String>? {
        var ds: DatagramSocket? = null
        try {
            ds = DatagramSocket(null)
            ds.reuseAddress = true
            ds.broadcast = true
            net.bindSocket(ds)
            ds.bind(InetSocketAddress(UDP_PORT))
            ds.soTimeout = 3000
            val buf = ByteArray(256)
            val end = System.currentTimeMillis() + 3000
            while (System.currentTimeMillis() < end) {
                val p = DatagramPacket(buf, buf.size)
                ds.receive(p)
                val parts = String(p.data, 0, p.length, Charsets.UTF_8).trim().split(" ", limit = 3)
                if (parts.size >= 2 && parts[0] == "WRISTMOUSE") {
                    val port = parts[1].toIntOrNull() ?: continue
                    val host = p.address.hostAddress ?: continue
                    return Triple(host, port, parts.getOrElse(2) { host })
                }
            }
        } catch (_: SocketTimeoutException) {
        } catch (e: Exception) {
            Log.w(WristService.TAG, "beacon listen failed", e)
        } finally {
            ds?.close()
        }
        val host = prefs.getString("host", null) ?: return null
        val port = prefs.getInt("port", 47320)
        return Triple(host, port, prefs.getString("name", host) ?: host)
    }

    private fun connect(net: Network, host: String, port: Int, name: String) {
        val s = net.socketFactory.createSocket() as Socket
        try {
            s.tcpNoDelay = true
            s.connect(InetSocketAddress(host, port), 3000)
            s.soTimeout = 8000
        } catch (e: Exception) {
            try { s.close() } catch (_: Exception) {}
            return
        }
        prefs.edit().putString("host", host).putInt("port", port).putString("name", name).apply()
        outQ.clear()
        socket = s
        Status.wifi = "Wi-Fi: connected to $name"
        onConnected()

        val writer = Thread({
            try {
                val out = BufferedOutputStream(s.getOutputStream(), 16 * 1024)
                while (!s.isClosed) {
                    val first = outQ.poll(1, TimeUnit.SECONDS) ?: continue
                    out.write((first + "\n").toByteArray(Charsets.UTF_8))
                    while (true) {
                        val next = outQ.poll() ?: break
                        out.write((next + "\n").toByteArray(Charsets.UTF_8))
                    }
                    out.flush()
                }
            } catch (_: Exception) {
            } finally {
                closeSocket(s)
            }
        }, "wm-wifi-tx")
        writer.start()

        try {
            val reader = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
            while (running) {
                val line = reader.readLine() ?: break
                onLine(line)
            }
        } catch (_: Exception) {
        } finally {
            closeSocket(s)
            Status.wifi = "Wi-Fi: disconnected"
        }
    }

    /** Close [only] if given, else whatever is current. */
    @Synchronized
    private fun closeSocket(only: Socket? = null) {
        val s = socket
        if (only != null && s !== only) {
            try { only.close() } catch (_: Exception) {}
            return
        }
        socket = null
        try { s?.close() } catch (_: Exception) {}
    }
}
