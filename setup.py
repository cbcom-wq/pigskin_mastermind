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
        "espn-api>=0.7.0",
        "fastapi>=0.109.0",
        "uvicorn[standard]>=0.27.0",
        "sqlalchemy>=2.0.25",
        "jinja2>=3.1.3",
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
