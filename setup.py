from setuptools import setup, find_packages

setup(
    name="pigskin_mastermind",
    version="0.1.0",
    description="Fantasy football research, entertainment, and management application",
    author="Pigskin Mastermind Team",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "requests>=2.31.0",
        "pandas>=2.0.0",
        "click>=8.1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "black>=23.7.0",
            "flake8>=6.1.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "pigskin=pigskin_mastermind.cli:main",
        ],
    },
    python_requires=">=3.8",
)
