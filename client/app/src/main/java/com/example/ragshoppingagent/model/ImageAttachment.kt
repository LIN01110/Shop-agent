/**
 * Purpose: 图片附件数据模型：定义图片URI、字节流、MIME类型、文件名，支持判断是否有内容。
 */

package com.example.ragshoppingagent.model

data class ImageAttachment(
    val uri: String,
    val bytes: ByteArray,
    val mimeType: String,
    val filename: String,
) {
    val hasContent: Boolean
        get() = bytes.isNotEmpty()
}
