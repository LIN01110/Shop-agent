/*
答辩重点 🔴（Android 状态管理）
任务：Day 2 任务 2.8 — 理解 SSE 事件流如何映射到 UI 状态。
核心：ChatUiState 管理 messages / products / comparison / cart / isStreaming；
      sendMessage() 构建请求 → 上传图片 → repository.streamChat()；
      buildCallbacks() 处理 6 种 SSE 事件：
      onToken 逐字追加，onProduct 商品卡片，onComparison 对比面板，
      onCart 购物车面板，onImageAnalysis 图片分析摘要，onDone 结束流式状态。
高频追问：
  - “Android 端怎么把 SSE 事件流映射到 UI 状态更新？”
    → StateFlow + ChatStreamCallbacks 多路分发
  - “sendMessage 的完整链路？”
    → 构建请求 → 图片上传 → repository.streamChat() → SSE → callbacks → update UI
*/

package com.example.ragshoppingagent.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.ragshoppingagent.model.CartState
import com.example.ragshoppingagent.model.ChatMessage
import com.example.ragshoppingagent.model.ChatSessionSummary
import com.example.ragshoppingagent.model.ComparisonCard
import com.example.ragshoppingagent.model.ImageAttachment
import com.example.ragshoppingagent.model.ProductCard
import com.example.ragshoppingagent.model.Role
import com.example.ragshoppingagent.repository.ChatRepository
import com.example.ragshoppingagent.repository.ChatStreamCallbacks
import com.example.ragshoppingagent.repository.UploadedImage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

data class ChatUiState(
    // 答辩：Android 端 UI 状态全集，对应后端 SSE 事件类型
    val messages: List<ChatMessage> = emptyList(),        // token / 图片摘要 / 错误消息
    val products: List<ProductCard> = emptyList(),        // product_card 事件累积
    val comparison: ComparisonCard? = null,               // comparison 事件
    val cart: CartState? = null,                          // cart 事件
    val input: String = "",
    val isStreaming: Boolean = false,                     // 控制加载态、按钮禁用
    val selectedImage: ImageAttachment? = null,
    val sessions: List<ChatSessionSummary> = emptyList(),
    val activeSessionId: String = "",
    val isLoadingSessions: Boolean = false,
)

class ChatViewModel : ViewModel() {
    private val repository = ChatRepository()
    private var sessionId = UUID.randomUUID().toString()
    private var replaceProductsOnNextProduct = false

    private val _uiState = MutableStateFlow(ChatUiState(activeSessionId = sessionId))
    val uiState: StateFlow<ChatUiState> = _uiState

    init {
        refreshSessions()
    }

    fun onInputChange(value: String) {
        _uiState.update { it.copy(input = value) }
    }

    fun attachImage(uri: String, bytes: ByteArray, mimeType: String, name: String) {
        _uiState.update {
            it.copy(
                selectedImage = ImageAttachment(
                    uri = uri,
                    bytes = bytes,
                    mimeType = mimeType,
                    filename = name,
                ),
            )
        }
    }

    fun clearImage() {
        _uiState.update { it.copy(selectedImage = null) }
    }

    fun sendMessage() {
        // 答辩：完整链路起点——构建请求 → 上传图片 → repository.streamChat() → SSE 回调更新 UI
        val current = _uiState.value
        val uploadCandidate = current.selectedImage?.takeIf { it.hasContent }
        val hasImage = uploadCandidate != null
        val text = current.input.trim()
        if ((text.isEmpty() && !hasImage) || current.isStreaming) return
        val messageText = text.ifEmpty { "我想找图片里的同款或相似商品" }

        val assistantId = UUID.randomUUID().toString()
        replaceProductsOnNextProduct = true  // 新回复的首个 product_card 替换旧列表
        _uiState.update {
            it.copy(
                input = "",
                isStreaming = true,
                products = emptyList(),
                comparison = null,
                selectedImage = null,
                messages = it.messages +
                    ChatMessage(
                        UUID.randomUUID().toString(),
                        Role.User,
                        if (hasImage) "$messageText\n[已附加图片]" else messageText,
                    ) +
                    ChatMessage(assistantId, Role.Assistant, "", isThinking = true),
            )
        }

        viewModelScope.launch {
            // 有图片时先上传，拿到 image_id 后再走 /chat
            val uploadedImage = if (uploadCandidate != null) {
                uploadSelectedImageOrStop(uploadCandidate, assistantId) ?: return@launch
            } else {
                null
            }

            repository.streamChat(
                sessionId = sessionId,
                message = messageText,
                callbacks = buildCallbacks(assistantId),  // 把 SSE 事件映射到 UI 状态
                uploadedImage = uploadedImage,
            )
        }
    }

    fun addProductToCart(product: ProductCard) {
        changeProductQuantity(product.id, 1)
    }

    fun incrementProductInCart(product: ProductCard) {
        changeProductQuantity(product.id, 1)
    }

    fun decrementProductInCart(product: ProductCard) {
        changeProductQuantity(product.id, -1)
    }

    fun refreshCart() {
        viewModelScope.launch {
            try {
                val cart = repository.getCart(sessionId)
                _uiState.update { it.copy(cart = cart) }
            } catch (_: Throwable) {
                // The cart button should stay usable even if the backend is temporarily unavailable.
            }
        }
    }

    fun resetSession() {
        val oldSessionId = sessionId
        sessionId = UUID.randomUUID().toString()
        val sessions = _uiState.value.sessions.filterNot { it.sessionId == oldSessionId }
        _uiState.value = ChatUiState(
            sessions = sessions,
            activeSessionId = sessionId,
        )
        viewModelScope.launch {
            try {
                repository.resetSession(oldSessionId)
                refreshSessions()
            } catch (_: Throwable) {
                // The local session has already rotated, so stale backend state will not be reused.
            }
        }
    }

    fun newSession() {
        if (_uiState.value.isStreaming) return
        sessionId = UUID.randomUUID().toString()
        _uiState.value = ChatUiState(
            sessions = _uiState.value.sessions,
            activeSessionId = sessionId,
        )
    }

    fun refreshSessions() {
        _uiState.update { it.copy(isLoadingSessions = true) }
        viewModelScope.launch {
            try {
                val sessions = repository.listSessions()
                _uiState.update {
                    it.copy(
                        sessions = sessions,
                        isLoadingSessions = false,
                        activeSessionId = sessionId,
                    )
                }
            } catch (_: Throwable) {
                _uiState.update { it.copy(isLoadingSessions = false) }
            }
        }
    }

    fun openSession(targetSessionId: String) {
        if (targetSessionId.isBlank() || _uiState.value.isStreaming) return
        _uiState.update { it.copy(isLoadingSessions = true) }
        viewModelScope.launch {
            try {
                val snapshot = repository.getSessionSnapshot(targetSessionId)
                sessionId = snapshot.sessionId
                _uiState.value = ChatUiState(
                    messages = snapshot.messages,
                    products = snapshot.products,
                    cart = snapshot.cart,
                    sessions = _uiState.value.sessions,
                    activeSessionId = sessionId,
                    isLoadingSessions = false,
                )
                refreshSessions()
            } catch (_: Throwable) {
                _uiState.update { it.copy(isLoadingSessions = false) }
            }
        }
    }

    private fun changeProductQuantity(productId: String, quantityDelta: Int) {
        if (productId.isBlank() || quantityDelta == 0) return

        viewModelScope.launch {
            try {
                val updatedCart = repository.mutateCartItem(
                    sessionId = sessionId,
                    productId = productId,
                    quantityDelta = quantityDelta,
                )
                _uiState.update { it.copy(cart = updatedCart) }
                refreshSessions()
            } catch (_: Throwable) {
                // Keep cart controls quiet; chat history should not be polluted by button actions.
            }
        }
    }

    private fun buildCallbacks(assistantId: String): ChatStreamCallbacks {
        // 答辩：6 种 SSE 事件 → UI 状态更新映射
        return ChatStreamCallbacks(
            onToken = { token -> appendAssistantToken(assistantId, token) },          // 逐字追加回复
            onProduct = { product -> appendProductCard(product) },                    // 商品卡片
            onComparison = { comparison -> _uiState.update { it.copy(comparison = comparison) } },  // 对比面板
            onCart = { cart -> _uiState.update { it.copy(cart = cart) } },            // 购物车面板
            onImageAnalysis = { summary ->
                // 图片分析摘要，作为 Assistant 消息的一部分展示
                if (summary.isNotBlank()) {
                    appendAssistantToken(assistantId, "$summary\n")
                }
            },
            onStatus = { markAssistantThinking(assistantId) },  // 显示“正在思考”
            onDone = {
                // 流结束：清理 thinking、刷新购物车/会话列表
                clearAssistantThinking(assistantId)
                replaceProductsOnNextProduct = false
                _uiState.update { it.copy(isStreaming = false) }
                refreshCart()
                refreshSessions()
            },
            onError = { error ->
                appendAssistantToken(assistantId, "\n请求失败：${error.message ?: "未知错误"}")
                replaceProductsOnNextProduct = false
                _uiState.update { it.copy(isStreaming = false) }
            },
        )
    }

    private fun appendProductCard(product: ProductCard) {
        _uiState.update { state ->
            // 新一次回复的第一个 product_card 替换旧列表；后续追加（例如一次返回多个商品）
            val nextProducts = if (replaceProductsOnNextProduct) {
                replaceProductsOnNextProduct = false
                listOf(product)
            } else {
                state.products + product
            }
            state.copy(products = nextProducts)
        }
    }

    private suspend fun uploadSelectedImageOrStop(
        attachment: ImageAttachment,
        assistantId: String,
    ): UploadedImage? {
        return try {
            repository.uploadImage(sessionId, attachment)
        } catch (error: Throwable) {
            appendAssistantToken(assistantId, "\n图片上传失败：${error.message ?: "未知错误"}")
            _uiState.update { it.copy(isStreaming = false) }
            null
        }
    }

    private fun appendAssistantToken(messageId: String, token: String) {
        _uiState.update { state ->
            state.copy(
                messages = state.messages.map { message ->
                    if (message.id == messageId) {
                        message.copy(text = message.text + token, isThinking = false)
                    } else {
                        message
                    }
                },
            )
        }
    }

    private fun markAssistantThinking(messageId: String) {
        _uiState.update { state ->
            state.copy(
                messages = state.messages.map { message ->
                    if (message.id == messageId && message.text.isBlank()) {
                        message.copy(isThinking = true)
                    } else {
                        message
                    }
                },
            )
        }
    }

    private fun clearAssistantThinking(messageId: String) {
        _uiState.update { state ->
            state.copy(
                messages = state.messages.map { message ->
                    if (message.id == messageId) {
                        message.copy(isThinking = false)
                    } else {
                        message
                    }
                },
            )
        }
    }
}
