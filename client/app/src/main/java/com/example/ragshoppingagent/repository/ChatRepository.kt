/**
 * Purpose: 聊天仓库层：封装SSE请求构造、图片上传、会话管理、商品/对比/购物车数据解析，连接ViewModel与网络层。
 */

package com.example.ragshoppingagent.repository

import com.example.ragshoppingagent.model.CartState
import com.example.ragshoppingagent.model.ChatSessionSnapshot
import com.example.ragshoppingagent.model.ChatSessionSummary
import com.example.ragshoppingagent.model.ComparisonCard
import com.example.ragshoppingagent.model.ImageAttachment
import com.example.ragshoppingagent.model.ProductCard
import com.example.ragshoppingagent.network.ChatSseClient
import okhttp3.sse.EventSource

data class UploadedImage(
    val imageId: String,
    val mimeType: String,
    val filename: String,
)

data class ChatStreamCallbacks(
    val onToken: (String) -> Unit,
    val onProduct: (ProductCard) -> Unit,
    val onComparison: (ComparisonCard) -> Unit,
    val onCart: (CartState) -> Unit,
    val onImageAnalysis: (String) -> Unit,
    val onStatus: (String) -> Unit,
    val onDone: () -> Unit,
    val onError: (Throwable) -> Unit,
)

class ChatRepository(
    private val client: ChatSseClient = ChatSseClient(),
) {
    suspend fun uploadImage(sessionId: String, attachment: ImageAttachment): UploadedImage {
        val imageId = client.uploadImage(
            sessionId = sessionId,
            imageBytes = attachment.bytes,
            mimeType = attachment.mimeType,
            filename = attachment.filename,
        )
        return UploadedImage(
            imageId = imageId,
            mimeType = attachment.mimeType,
            filename = attachment.filename,
        )
    }

    suspend fun mutateCartItem(
        sessionId: String,
        productId: String,
        quantityDelta: Int,
    ): CartState {
        return client.mutateCartItem(
            sessionId = sessionId,
            productId = productId,
            quantityDelta = quantityDelta,
        )
    }

    suspend fun getCart(sessionId: String): CartState {
        return client.getCart(sessionId)
    }

    suspend fun resetSession(sessionId: String) {
        client.resetSession(sessionId)
    }

    suspend fun listSessions(limit: Int = 30): List<ChatSessionSummary> {
        return client.listSessions(limit)
    }

    suspend fun getSessionSnapshot(sessionId: String): ChatSessionSnapshot {
        return client.getSessionSnapshot(sessionId)
    }

    fun streamChat(
        sessionId: String,
        message: String,
        callbacks: ChatStreamCallbacks,
        uploadedImage: UploadedImage? = null,
    ): EventSource {
        return client.send(
            sessionId = sessionId,
            message = message,
            onToken = callbacks.onToken,
            onProduct = callbacks.onProduct,
            onComparison = callbacks.onComparison,
            onCart = callbacks.onCart,
            onImageAnalysis = callbacks.onImageAnalysis,
            onStatus = callbacks.onStatus,
            onDone = callbacks.onDone,
            onError = callbacks.onError,
            imageId = uploadedImage?.imageId.orEmpty(),
            imageMimeType = uploadedImage?.mimeType.orEmpty(),
            imageFilename = uploadedImage?.filename.orEmpty(),
        )
    }
}
