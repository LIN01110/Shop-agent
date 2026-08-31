/*
答辩重点 🟠（Android SSE 客户端）
任务：Day 2 任务 2.9 — 理解 OkHttp SSE 实现。
核心：建立 SSE 连接 → 逐行读取 event: xxx / data: {json} → 按 event 类型分发到 callbacks；
      处理网络中断、解析失败等错误。
高频追问：
  - “SSE 和 WebSocket 在这个项目中的取舍？”
    → SSE 适合服务器单向推流，Android 实现简单；WebSocket 用于双工场景
  - “断线重连怎么做？”
    → 在错误回调中通知 ViewModel，业务层可重新发起请求
*/

package com.example.ragshoppingagent.network

import com.example.ragshoppingagent.model.CartState
import com.example.ragshoppingagent.model.ChatSessionSnapshot
import com.example.ragshoppingagent.model.ChatSessionSummary
import com.example.ragshoppingagent.model.ComparisonCard
import com.example.ragshoppingagent.model.ProductCard
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class ChatSseClient(
    private val baseUrl: String = BackendConfig.LOCAL_REVERSE_PROXY_BASE_URL,
) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .build()

    suspend fun uploadImage(
        sessionId: String,
        imageBytes: ByteArray,
        mimeType: String,
        filename: String,
    ): String = withContext(Dispatchers.IO) {
        val safeMimeType = mimeType.ifBlank { "image/jpeg" }
        val safeFilename = filename.ifBlank { "picked_image.jpg" }
        val requestBody = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("session_id", sessionId)
            .addFormDataPart(
                "file",
                safeFilename,
                imageBytes.toRequestBody(safeMimeType.toMediaType()),
            )
            .build()

        val request = Request.Builder()
            .url("$baseUrl/uploads/images")
            .post(requestBody)
            .build()

        client.newCall(request).execute().use { response ->
            val bodyText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IllegalStateException("Upload failed: HTTP ${response.code} $bodyText")
            }
            JSONObject(bodyText).getString("image_id")
        }
    }

    suspend fun mutateCartItem(
        sessionId: String,
        productId: String,
        quantityDelta: Int,
    ): CartState = withContext(Dispatchers.IO) {
        val body = JSONObject()
            .put("session_id", sessionId)
            .put("product_id", productId)
            .put("quantity_delta", quantityDelta)
            .toString()
            .toRequestBody("application/json; charset=utf-8".toMediaType())

        val request = Request.Builder()
            .url("$baseUrl/cart/items")
            .post(body)
            .build()

        client.newCall(request).execute().use { response ->
            val bodyText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IllegalStateException("Cart update failed: HTTP ${response.code} $bodyText")
            }
            CartState.fromJson(JSONObject(bodyText))
        }
    }

    suspend fun getCart(sessionId: String): CartState = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$baseUrl/cart?session_id=${encodePath(sessionId)}")
            .get()
            .build()

        client.newCall(request).execute().use { response ->
            val bodyText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IllegalStateException("Cart fetch failed: HTTP ${response.code} $bodyText")
            }
            CartState.fromJson(JSONObject(bodyText))
        }
    }

    suspend fun resetSession(sessionId: String) = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$baseUrl/sessions/${encodePath(sessionId)}")
            .delete()
            .build()

        client.newCall(request).execute().use { response ->
            val bodyText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IllegalStateException("Session reset failed: HTTP ${response.code} $bodyText")
            }
        }
    }

    suspend fun listSessions(limit: Int = 30): List<ChatSessionSummary> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$baseUrl/sessions?limit=$limit")
            .get()
            .build()

        client.newCall(request).execute().use { response ->
            val bodyText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IllegalStateException("Session list failed: HTTP ${response.code} $bodyText")
            }
            val array = JSONObject(bodyText).optJSONArray("sessions") ?: return@withContext emptyList()
            val sessions = mutableListOf<ChatSessionSummary>()
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                sessions += ChatSessionSummary.fromJson(item)
            }
            sessions
        }
    }

    suspend fun getSessionSnapshot(sessionId: String): ChatSessionSnapshot = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$baseUrl/sessions/${encodePath(sessionId)}")
            .get()
            .build()

        client.newCall(request).execute().use { response ->
            val bodyText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IllegalStateException("Session snapshot failed: HTTP ${response.code} $bodyText")
            }
            ChatSessionSnapshot.fromJson(JSONObject(bodyText))
        }
    }

    fun send(
        sessionId: String,
        message: String,
        onToken: (String) -> Unit,
        onProduct: (ProductCard) -> Unit,
        onComparison: (ComparisonCard) -> Unit,
        onCart: (CartState) -> Unit,
        onImageAnalysis: (String) -> Unit,
        onStatus: (String) -> Unit,
        onDone: () -> Unit,
        onError: (Throwable) -> Unit,
        imageId: String = "",
        imageMimeType: String = "",
        imageFilename: String = "",
    ): EventSource {
        val body = JSONObject()
            .put("session_id", sessionId)
            .put("message", message)
            .apply {
                if (imageId.isNotBlank()) {
                    put("image_id", imageId)
                    put("image_mime_type", imageMimeType)
                    put("image_filename", imageFilename)
                }
            }
            .toString()
            .toRequestBody("application/json; charset=utf-8".toMediaType())

        val request = Request.Builder()
            .url("$baseUrl/chat")
            .post(body)
            .build()

        return EventSources.createFactory(client).newEventSource(
            request,
            object : EventSourceListener() {
                override fun onEvent(
                    eventSource: EventSource,
                    id: String?,
                    type: String?,
                    data: String,
                ) {
                    val json = JSONObject(data)
                    when (type) {
                        "token" -> onToken(json.optString("text"))
                        "product_card" -> onProduct(ProductCard.fromJson(json))
                        "comparison_card" -> onComparison(ComparisonCard.fromJson(json))
                        "cart_update" -> onCart(CartState.fromJson(json))
                        "image_analysis" -> onImageAnalysis(json.optString("summary"))
                        "status" -> onStatus(json.optString("text"))
                        "done" -> onDone()
                    }
                }

                override fun onFailure(
                    eventSource: EventSource,
                    t: Throwable?,
                    response: Response?,
                ) {
                    onError(t ?: IllegalStateException("SSE failed: ${response?.code}"))
                }
            },
        )
    }

    private fun encodePath(value: String): String {
        return URLEncoder.encode(value, "UTF-8").replace("+", "%20")
    }
}
