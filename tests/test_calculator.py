import json
import pytest

from src.agent import _handle_calculator


class TestBasicArithmetic:
    def test_addition(self):
        result = json.loads(_handle_calculator(["2 + 3"]))
        assert result[0]["result"] == 5.0

    def test_multiplication(self):
        result = json.loads(_handle_calculator(["1500000 * 0.20"]))
        assert result[0]["result"] == 300000.0

    def test_complex_expression(self):
        result = json.loads(_handle_calculator(["(1500000 * 0.20) + 5000"]))
        assert result[0]["result"] == 305000.0

    def test_exponentiation(self):
        result = json.loads(_handle_calculator(["(1 + 0.05)**30"]))
        assert abs(result[0]["result"] - 4.321942) < 0.001

    def test_modulo(self):
        result = json.loads(_handle_calculator(["10 % 3"]))
        assert result[0]["result"] == 1.0


class TestMathFunctions:
    def test_sqrt(self):
        result = json.loads(_handle_calculator(["sqrt(144)"]))
        assert result[0]["result"] == 12.0

    def test_log(self):
        result = json.loads(_handle_calculator(["log(e)"]))
        assert abs(result[0]["result"] - 1.0) < 1e-10

    def test_log10(self):
        result = json.loads(_handle_calculator(["log10(1000)"]))
        assert result[0]["result"] == 3.0

    def test_abs(self):
        result = json.loads(_handle_calculator(["abs(-42)"]))
        assert result[0]["result"] == 42.0

    def test_pi_constant(self):
        result = json.loads(_handle_calculator(["pi"]))
        assert abs(result[0]["result"] - 3.14159265) < 0.0001


class TestMultipleExpressions:
    def test_batch(self):
        result = json.loads(_handle_calculator([
            "1500000 * 0.20",
            "2000000 * 0.10",
            "sqrt(144)",
        ]))
        assert len(result) == 3
        assert result[0]["result"] == 300000.0
        assert result[1]["result"] == 200000.0
        assert result[2]["result"] == 12.0

    def test_partial_failure(self):
        result = json.loads(_handle_calculator([
            "2 + 2",
            "not_a_number",
        ]))
        assert len(result) == 2
        assert result[0]["result"] == 4.0
        assert "error" in result[1]

    def test_each_result_has_expression(self):
        result = json.loads(_handle_calculator(["1 + 1", "2 + 2"]))
        assert result[0]["expression"] == "1 + 1"
        assert result[1]["expression"] == "2 + 2"


class TestSafetyGuards:
    def test_expression_too_long(self):
        long_expr = "1 + " * 100 + "1"
        result = json.loads(_handle_calculator([long_expr]))
        assert "error" in result[0]
        assert "too long" in result[0]["error"].lower()

    def test_max_expressions_capped(self):
        exprs = [f"{i} + 1" for i in range(15)]
        result = json.loads(_handle_calculator(exprs))
        assert len(result) == 10

    def test_invalid_expression_returns_error(self):
        result = json.loads(_handle_calculator(["hello world"]))
        assert "error" in result[0]

    def test_empty_list(self):
        result = json.loads(_handle_calculator([]))
        assert result == []


class TestAccountingScenarios:
    def test_pdv_20_percent(self):
        result = json.loads(_handle_calculator(["1500000 * 0.20"]))
        assert result[0]["result"] == 300000.0

    def test_gross_to_net_salary(self):
        """bruto - (bruto - neoporezivi_deo) * stopa"""
        result = json.loads(_handle_calculator([
            "(150000 - 21712) * 0.10",
        ]))
        assert abs(result[0]["result"] - 12828.8) < 0.01

    def test_compound_interest(self):
        """FV = PV * (1 + r)^n"""
        result = json.loads(_handle_calculator([
            "1000000 * (1 + 0.05)**10",
        ]))
        assert abs(result[0]["result"] - 1628894.63) < 1.0
