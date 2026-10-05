"""JSON decoding limits produce predictable errors without changing source files."""
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal, InvalidOperation, MAX_EMAX, MIN_ETINY, localcontext
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from public_data_sentinel.cli import main
from public_data_sentinel.validation import load_json, validate


OUTSIDE_RANGE = '1e999999999999999999999999999999'
VALID_DATA = '[{"amount":1}]'
VALID_CONTRACT = '{"columns":{"amount":{"type":"decimal"}}}'


class JsonErrorTests(unittest.TestCase):
    def test_json_float_range_failure_is_a_value_error(self):
        for number in (OUTSIDE_RANGE, '-' + OUTSIDE_RANGE, '1e-999999999999999999999999999999'):
            with self.subTest(number=number), self.assertRaises(ValueError):
                load_json(number)

    def test_out_of_range_float_cannot_become_nan_with_traps_disabled(self):
        with localcontext() as context:
            context.traps[InvalidOperation] = False
            with self.assertRaises(ValueError):
                load_json(OUTSIDE_RANGE)

    def test_parser_recursion_error_is_a_value_error(self):
        with patch('public_data_sentinel.validation.json.loads',
                   side_effect=RecursionError('synthetic parser limit')):
            with self.assertRaisesRegex(ValueError, 'JSON nesting'):
                load_json('[[]]')

    def test_native_nesting_capacity_is_preserved(self):
        for text in ('[' * 10000 + '0' + ']' * 10000,
                     '{"next":' * 10000 + '0' + '}' * 10000):
            with self.subTest(container=text[0]):
                try:
                    json.loads(text)
                except RecursionError:
                    with self.assertRaisesRegex(ValueError, 'JSON nesting'):
                        load_json(text)
                else:
                    # Newer parsers may support this depth; retain their capacity.
                    value = load_json(text)
                    for _ in range(10000):
                        value = value[0] if isinstance(value, list) else value['next']
                    self.assertEqual(value, 0)

    def test_valid_large_exact_numbers_and_types_are_unchanged(self):
        value = load_json('[1e400,-1e-400,0.12345678901234567890123456789,9007199254740993,true]')
        self.assertEqual(value, [Decimal('1e400'), Decimal('-1e-400'), Decimal('0.12345678901234567890123456789'), 9007199254740993, True])
        self.assertIsInstance(value[3], int)
        self.assertNotIsInstance(value[3], bool)
        self.assertIs(value[4], True)

    def test_valid_numbers_ignore_restrictive_decimal_arithmetic_context(self):
        with localcontext() as context:
            context.prec = 1
            context.Emax = 1
            context.Emin = -1
            for signal in context.traps:
                context.traps[signal] = True
            value = load_json('[1.23456789,1e400,-1e-400]')
        self.assertEqual(value, [Decimal('1.23456789'), Decimal('1e400'), Decimal('-1e-400')])

    def test_supported_decimal_boundaries_and_zero_sign_are_preserved(self):
        for exponent in (MAX_EMAX, MIN_ETINY):
            for coefficient in ('1', '-1', '0', '-0'):
                text = f'{coefficient}e{exponent}'
                with self.subTest(text=text):
                    self.assertEqual(load_json(text).as_tuple(), Decimal(text).as_tuple())
        for text in ('-0.00', '12.3000', '-0.12345678901234567890123456789'):
            with self.subTest(text=text):
                self.assertEqual(load_json(text).as_tuple(), Decimal(text).as_tuple())

    def test_ignored_extra_column_cannot_hide_nonfinite_decode_result(self):
        contract = {'columns': {'id': {'type': 'string'}}, 'allow_extra_columns': True}
        text = '[{"id":"ok","extra":' + OUTSIDE_RANGE + '}]'
        with localcontext() as context:
            context.traps[InvalidOperation] = False
            with self.assertRaises(ValueError):
                validate(load_json(text), contract)

    def test_zero_with_out_of_range_exponent_is_also_rejected(self):
        for text in ('0e999999999999999999999999999999', '-0e-999999999999999999999999999999'):
            for traps_enabled in (True, False):
                with self.subTest(text=text, traps_enabled=traps_enabled), localcontext() as context:
                    context.traps[InvalidOperation] = traps_enabled
                    with self.assertRaises(ValueError):
                        load_json(text)

    def test_invalid_json_does_not_create_a_new_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / 'input.json'
            contract = folder / 'contract.json'
            output = folder / 'report.json'
            source.write_text('[{"amount":' + OUTSIDE_RANGE + '}]')
            contract.write_text(VALID_CONTRACT)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main([str(source), '--contract', str(contract), '--output', str(output)])
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())

    def test_existing_duplicate_constant_and_syntax_errors_remain_value_errors(self):
        for text, message in (('{"a":1,"a":2}', 'Duplicate JSON key: a'),
                              ('[NaN]', 'Nonstandard JSON constant: NaN'),
                              ('[Infinity]', 'Nonstandard JSON constant: Infinity')):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                load_json(text)
        with self.assertRaises(json.JSONDecodeError):
            load_json('{')

    def test_cli_input_and_contract_failures_preserve_existing_files(self):
        deep = '[' * 10000 + '0' + ']' * 10000
        original_loads = json.loads

        def decode(text, *args, **kwargs):
            # Parser depth limits vary across supported Python versions.
            if text == deep:
                raise RecursionError('synthetic parser limit')
            return original_loads(text, *args, **kwargs)

        for label, data, contract in (
            ('input-range', '[{"amount":' + OUTSIDE_RANGE + '}]', VALID_CONTRACT),
            ('contract-range', VALID_DATA, '{"columns":{"amount":{"type":"decimal","minimum":' + OUTSIDE_RANGE + '}}}'),
            ('input-depth', deep, VALID_CONTRACT),
            ('contract-depth', VALID_DATA, deep),
        ):
            for format_name in ('json', 'markdown'):
                with self.subTest(label=label, format=format_name), tempfile.TemporaryDirectory() as temporary:
                    folder = Path(temporary)
                    source, rules, output = (folder / name for name in ('input.json', 'contract.json', 'report.txt'))
                    source.write_text(data)
                    rules.write_text(contract)
                    output.write_text('existing report')
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr), patch(
                        'public_data_sentinel.validation.json.loads', side_effect=decode
                    ):
                        code = main([str(source), '--contract', str(rules), '--format', format_name, '--output', str(output)])
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout.getvalue(), '')
                    self.assertTrue(stderr.getvalue().startswith('data-sentinel: '))
                    self.assertNotIn('Traceback', stderr.getvalue())
                    self.assertEqual(output.read_text(), 'existing report')
                    self.assertEqual(source.read_text(), data)
                    self.assertEqual(rules.read_text(), contract)


if __name__ == '__main__':
    unittest.main()
