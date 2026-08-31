/**
 * Purpose: 聊天消息数据模型：定义消息ID、角色（user/bot/assistant）、文本内容和思考状态。
 */

package com.example.ragshoppingagent.model

data class ChatMessage(
    val id: String,
    val role: Role,
    val text: String,
    val isThinking: Boolean = false,
)

enum class Role {
    User,
    Assistant,
}
