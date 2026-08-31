/**
 * Purpose: 商品对比卡片数据模型：定义对比标题、查询条件、对比产品列表、推荐建议和胜出项。
 */

package com.example.ragshoppingagent.model

import org.json.JSONArray
import org.json.JSONObject

data class ComparisonCard(
    val title: String,
    val query: String,
    val products: List<ComparisonProduct>,
    val recommendation: ComparisonRecommendation,
) {
    companion object {
        fun fromJson(json: JSONObject): ComparisonCard {
            return ComparisonCard(
                title = json.optString("title", "商品对比"),
                query = json.optString("query"),
                products = parseProducts(json.optJSONArray("products")),
                recommendation = ComparisonRecommendation.fromJson(json.optJSONObject("recommendation")),
            )
        }

        private fun parseProducts(array: JSONArray?): List<ComparisonProduct> {
            if (array == null) return emptyList()

            val products = mutableListOf<ComparisonProduct>()
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                products += ComparisonProduct.fromJson(item)
            }
            return products
        }
    }
}

data class ComparisonProduct(
    val id: String,
    val name: String,
    val brand: String,
    val category: String,
    val price: Double,
    val reason: String,
    val strengths: List<String>,
    val tradeoffs: List<String>,
) {
    companion object {
        fun fromJson(json: JSONObject): ComparisonProduct {
            return ComparisonProduct(
                id = json.optString("id"),
                name = json.optString("name"),
                brand = json.optString("brand"),
                category = json.optString("category"),
                price = json.optDouble("price"),
                reason = json.optString("reason"),
                strengths = json.optJSONArray("strengths").toStringList(),
                tradeoffs = json.optJSONArray("tradeoffs").toStringList(),
            )
        }
    }
}

data class ComparisonRecommendation(
    val productId: String,
    val productName: String,
    val focus: String,
    val summary: String,
) {
    companion object {
        fun fromJson(json: JSONObject?): ComparisonRecommendation {
            return ComparisonRecommendation(
                productId = json?.optString("product_id").orEmpty(),
                productName = json?.optString("product_name").orEmpty(),
                focus = json?.optString("focus").orEmpty(),
                summary = json?.optString("summary").orEmpty(),
            )
        }
    }
}

private fun JSONArray?.toStringList(): List<String> {
    if (this == null) return emptyList()

    val values = mutableListOf<String>()
    for (index in 0 until length()) {
        val value = optString(index)
        if (value.isNotBlank()) {
            values += value
        }
    }
    return values
}
