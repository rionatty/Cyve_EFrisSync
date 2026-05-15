from setuptools import setup, find_packages


# Read only the third-party dependencies.
# Never include 'frappe' here — it lives in the bench environment already
# and uv/pip will error trying to resolve it as a URL dependency.
with open("requirements.txt") as f:
    install_requires = [
        line.strip()
        for line in f
        if line.strip() and not line.startswith("#")
    ]


setup(
    name="efris_integration",
    version="1.0.0",
    description="URA EFRIS Integration for ERPNext v16",
    author="CyveTech",
    author_email="info@cyvetech.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
    python_requires=">=3.10",
)
