from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in colprocah_colegio/__init__.py
from colprocah_colegio import __version__ as version

setup(
	name="colprocah_colegio",
	version=version,
	description="Personalizacion Colprocah",
	author="Cuatrocubos Soluciones",
	author_email="jgiron@cuatrocubos.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
