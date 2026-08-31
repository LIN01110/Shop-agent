<!-- Purpose: 客户端模块说明文档。 -->

# Android Client

Native Android scaffold built with Kotlin and Jetpack Compose.

## Notes

- Physical-device backend URL defaults to `http://127.0.0.1:8000` and uses `adb reverse tcp:8000 tcp:8000`.
- If testing on an emulator, change `baseUrl` in `ChatSseClient` to `http://10.0.2.2:8000`.
- The client listens to SSE events: `token`, `product_card`, `comparison_card`, `cart_update`, and `done`.
- `comparison_card` is rendered as a compact comparison panel above the product carousel.
- `cart_update` is rendered as a compact cart panel with item count, total price, and clickable recent items.
