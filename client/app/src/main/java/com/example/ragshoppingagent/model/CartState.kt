/**
 * Purpose: 购物车状态数据模型：定义购物车商品列表、总价、总数量、空状态等字段，支持从JSON解析。
 */

package com.example.ragshoppingagent.model

import org.json.JSONArray
import org.json.JSONObject

data class CartState(
    val items: List<CartItem>,
    val totalQuantity: Int,
    val totalPrice: Double,
    val isEmpty: Boolean,
) {
    fun quantityFor(productId: String): Int {
        return items.firstOrNull { it.productId == productId }?.quantity ?: 0
    }

    companion object {
        fun empty(): CartState {
            return CartState(
                items = emptyList(),
                totalQuantity = 0,
                totalPrice = 0.0,
                isEmpty = true,
            )
        }

        fun fromJson(json: JSONObject): CartState {
            return CartState(
                items = parseItems(json.optJSONArray("items")),
                totalQuantity = json.optInt("total_quantity"),
                totalPrice = json.optDouble("total_price"),
                isEmpty = json.optBoolean("is_empty"),
            )
        }

        private fun parseItems(array: JSONArray?): List<CartItem> {
            if (array == null) return emptyList()

            val items = mutableListOf<CartItem>()
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                items += CartItem.fromJson(item)
            }
            return items
        }
    }
}

data class CartItem(
    val productId: String,
    val name: String,
    val brand: String,
    val category: String,
    val price: Double,
    val quantity: Int,
    val imageUrl: String,
    val detailUrl: String,
) {
    companion object {
        fun fromJson(json: JSONObject): CartItem {
            return CartItem(
                productId = json.optString("product_id"),
                name = json.optString("name"),
                brand = json.optString("brand"),
                category = json.optString("category"),
                price = json.optDouble("price"),
                quantity = json.optInt("quantity"),
                imageUrl = json.optString("image_url"),
                detailUrl = json.optString("detail_url"),
            )
        }
    }
}
