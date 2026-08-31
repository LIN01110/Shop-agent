/**
 * Purpose: 商品卡片UI组件：Compose实现，展示商品图片、名称、价格、评分、加购按钮。
 */

package com.example.ragshoppingagent.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AddShoppingCart
import androidx.compose.material.icons.filled.OpenInNew
import androidx.compose.material.icons.filled.Remove
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.example.ragshoppingagent.model.ProductCard

@Composable
fun ProductCardItem(
    product: ProductCard,
    cartQuantity: Int,
    onOpen: (ProductCard) -> Unit,
    onAddToCart: (ProductCard) -> Unit,
    onIncrementCart: (ProductCard) -> Unit,
    onDecrementCart: (ProductCard) -> Unit,
) {
    Card(
        onClick = { onOpen(product) },
        modifier = Modifier.width(232.dp),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            modifier = Modifier
                .heightIn(min = 260.dp)
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            ProductImage(
                imageUrl = product.imageUrl,
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(1.25f)
                    .clip(RoundedCornerShape(6.dp)),
            )
            Text(
                text = product.name,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                maxLines = 3,
            )
            Text(text = product.brand, style = MaterialTheme.typography.labelMedium)
            Text(
                text = "¥${product.price.toInt()}",
                color = MaterialTheme.colorScheme.primary,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = product.reason,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 3,
            )
            CartQuantityControl(
                quantity = cartQuantity,
                onAdd = { onAddToCart(product) },
                onIncrement = { onIncrementCart(product) },
                onDecrement = { onDecrementCart(product) },
            )
        }
    }
}

@Composable
fun ProductDetailDialog(
    product: ProductCard,
    cartQuantity: Int,
    onDismiss: () -> Unit,
    onAddToCart: (ProductCard) -> Unit,
    onIncrementCart: (ProductCard) -> Unit,
    onDecrementCart: (ProductCard) -> Unit,
) {
    val uriHandler = LocalUriHandler.current

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(text = product.name, style = MaterialTheme.typography.titleMedium)
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                ProductImage(
                    imageUrl = product.imageUrl,
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(1.35f)
                        .clip(RoundedCornerShape(8.dp)),
                )
                Text(text = "${product.brand} · ${product.category}", style = MaterialTheme.typography.labelLarge)
                Text(
                    text = "¥${product.price.toInt()}",
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold,
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(text = product.reason, style = MaterialTheme.typography.bodyMedium)
            }
        },
        confirmButton = {
            CartQuantityControl(
                quantity = cartQuantity,
                onAdd = { onAddToCart(product) },
                onIncrement = { onIncrementCart(product) },
                onDecrement = { onDecrementCart(product) },
            )
        },
        dismissButton = {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = onDismiss) {
                    Text("关闭")
                }
            if (product.detailUrl.isNotBlank()) {
                TextButton(onClick = { uriHandler.openUri(product.detailUrl) }) {
                        Icon(imageVector = Icons.Filled.OpenInNew, contentDescription = null)
                        Text(
                            text = "查看详情",
                            modifier = Modifier.padding(start = 4.dp),
                        )
                    }
                }
            }
        },
    )
}

@Composable
private fun CartQuantityControl(
    quantity: Int,
    onAdd: () -> Unit,
    onIncrement: () -> Unit,
    onDecrement: () -> Unit,
) {
    if (quantity <= 0) {
        FilledTonalButton(
            onClick = onAdd,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(6.dp),
        ) {
            Icon(
                imageVector = Icons.Filled.AddShoppingCart,
                contentDescription = null,
            )
            Text(
                text = "加入购物车",
                modifier = Modifier.padding(start = 6.dp),
                style = MaterialTheme.typography.labelLarge,
            )
        }
        return
    }

    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.primaryContainer,
        contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
        shape = RoundedCornerShape(6.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 40.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(
                onClick = onDecrement,
                modifier = Modifier.size(40.dp),
            ) {
                Icon(imageVector = Icons.Filled.Remove, contentDescription = "减少数量")
            }
            Text(
                text = "已加 $quantity",
                modifier = Modifier.weight(1f),
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
            )
            IconButton(
                onClick = onIncrement,
                modifier = Modifier.size(40.dp),
            ) {
                Icon(imageVector = Icons.Filled.Add, contentDescription = "增加数量")
            }
        }
    }
}

@Composable
fun ProductImage(imageUrl: String, modifier: Modifier = Modifier) {
    var failed by remember(imageUrl) { mutableStateOf(false) }

    if (imageUrl.isBlank() || failed) {
        Box(
            modifier = modifier.background(MaterialTheme.colorScheme.surfaceVariant),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = "暂无图片",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.labelMedium,
            )
        }
        return
    }

    AsyncImage(
        model = imageUrl,
        contentDescription = null,
        contentScale = ContentScale.Crop,
        onError = { failed = true },
        modifier = modifier.background(MaterialTheme.colorScheme.surfaceVariant),
    )
}
