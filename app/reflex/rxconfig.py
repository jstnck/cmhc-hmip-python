import reflex as rx

config = rx.Config(
    app_name="cmhc_portal",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
    ]
)