/**
 * Purpose: 应用主题定义：配置Material颜色方案（主色、次色、背景色等）。
 */

package com.example.ragshoppingagent.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF176B5B),
    secondary = Color(0xFF6A5B2F),
    tertiary = Color(0xFF7A4152),
    surface = Color(0xFFFCFCF8),
    background = Color(0xFFF7F8F4),
)

@Composable
fun RagShoppingAgentTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = LightColors,
        content = content,
    )
}
