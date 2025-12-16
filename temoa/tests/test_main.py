"""
quick test of some of the argument parsing

Written by:  J. F. Hyink
jeff@westernspark.us
https://westernspark.us
Created on:  11/26/23

Tools for Energy Model Optimization and Analysis (Temoa):
An open source framework for energy systems optimization modeling

Copyright (C) 2015,  NC State University

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

A complete copy of the GNU General Public License v2 (GPLv2) is available
in LICENSE.txt.  Users uncompressing this from an archive may not have
received this license file.  If not, see <http://www.gnu.org/licenses/>.
"""

import pytest

import main


def test_parse_args(tmp_path, capsys):
    config_file = tmp_path / 'config.toml'
    config_file.write_text('nada')

    # missing config
    with pytest.raises(AttributeError) as e:
        main.parse_args(f'--output_path {tmp_path}'.split())

    # bad file
    with pytest.raises(FileNotFoundError) as e:
        main.parse_args(f'--config sasquatch_bait.toml --output_path {tmp_path}'.split())

    # good file
    main.parse_args(f'--config {config_file} --output_path {tmp_path}'.split())

    # good output path
    options = main.parse_args(f'--config {config_file} --output_path {tmp_path}'.split())
    assert options.output_path == tmp_path

    # bad output path
    with pytest.raises(FileNotFoundError) as e:
        main.parse_args(f'--config {config_file} --output_path big_bird'.split())

    # options setting
    options = main.parse_args(f'--config {config_file} --output_path {tmp_path} -s -d -b'.split())
    assert all((options.silent, options.debug, options.build_only))
