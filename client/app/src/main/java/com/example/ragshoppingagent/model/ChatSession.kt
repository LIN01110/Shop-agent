/**
 * Purpose: 会话数据模型：定义会话摘要（标题、摘要、更新时间）和会话快照（完整状态、消息、商品、购物车）。
 */

package com.example.ragshoppingagent.model

import org.json.JSONArray
import org.json.JSONObject

data class ChatSessionSummary(
    val sessionId: String,
    val title: String,
    val summary: String,
    val updatedAt: Double,
    val messageCount: Int,
    val cartQuantity: Int,
) {
    companion object {
        fun fromJson(json: JSONObject): ChatSessionSummary {
            return ChatSessionSummary(
                sessionId = json.optString("session_id"),
                title = json.optString("title", "新对话"),
                summary = json.optString("summary"),
                updatedAt = json.optDouble("updated_at"),
                messageCount = json.optInt("message_count"),
                cartQuantity = json.optInt("cart_quantity"),
            )
        }
    }
}

data class ChatSessionSnapshot(
    val sessionId: String,
    val title: String,
    val summary: String,
    val messages: List<ChatMessage>,
    val products: List<ProductCard>,
    val cart: CartState,
) {
    companion object {
        fun fromJson(json: JSONObject): ChatSessionSnapshot {
            val sessionId = json.optString("session_id")
            return ChatSessionSnapshot(
                sessionId = sessionId,
                title = json.optString("title", "新对话"),
                summary = json.optString("summary"),
                messages = parseMessages(sessionId, json.optJSONArray("messages")),
                products = parseProducts(json.optJSONArray("products")),
                cart = CartState.fromJson(json.optJSONObject("cart") ?: JSONObject()),
            )
        }

        private fun parseMessages(sessionId: String, array: JSONArray?): List<ChatMessage> {
            if (array == null) return emptyList()

            val messages = mutableListOf<ChatMessage>()
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                val role = if (item.optString("role") == "user") Role.User else Role.Assistant
                val text = item.optString("content")
                if (text.isBlank()) continue
                messages += ChatMessage(
                    id = "history-$sessionId-$index",
                    role = role,
                    text = text,
                )
            }
            return messages
        }

        private fun parseProducts(array: JSONArray?): List<ProductCard> {
            if (array == null) return emptyList()

            val products = mutableListOf<ProductCard>()
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                products += ProductCard.fromJson(item)
            }
            return products
        }
    }
}
