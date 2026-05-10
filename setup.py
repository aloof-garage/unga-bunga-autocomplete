from setuptools import setup, find_packages

setup(
    name="unga_bunga_autocomplete",
    version="1.0.0",
    description="Production-grade autocomplete platform",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "rich>=13.0",
        "prompt_toolkit>=3.0",
    ],
    extras_require={
        "dev": ["pytest>=7.0", "pytest-asyncio>=0.21"],
    },
    entry_points={
        "console_scripts": [
            "unga-bunga=unga_bunga_autocomplete.__main__:main",
        ],
    },
)
