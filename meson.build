project(
  'namioto',
  'cpp',
  version: '0.1.0',
  default_options: ['cpp_std=c++17'],
)

py = import('python').find_installation(pure: false)

pybind11_inc = run_command(
  py, '-c', 'import pybind11; print(pybind11.get_include())', check: true
).stdout().strip()

vendor = include_directories(
  'vendor',
  'vendor/signalsmith-stretch',
)

py.extension_module(
  '_audio',
  files(
    'audio/module.cpp',
    'audio/engine.cpp',
    'audio/output.cpp',
    'audio/stretcher.cpp',
    'audio/miniaudio_impl.cpp',
  ),
  include_directories: [vendor, include_directories(pybind11_inc)],
  install: true,
  subdir: 'namioto',
)

install_subdir(
  'namioto',
  install_dir: py.get_install_dir(),
  exclude_directories: [
    '__pycache__',
    'analysis/__pycache__',
    'karaoke/__pycache__',
    'ui/__pycache__',
  ],
  exclude_files: ['**/*.pyc'],
)
