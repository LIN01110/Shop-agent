/**
 * Purpose: 应用入口Activity：继承ComponentActivity，通过setContent加载Compose聊天界面和主题。
 */

package com.example.ragshoppingagent

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.example.ragshoppingagent.ui.ChatRoute
import com.example.ragshoppingagent.ui.RagShoppingAgentTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            RagShoppingAgentTheme {
                ChatRoute()
            }
        }
    }
}
