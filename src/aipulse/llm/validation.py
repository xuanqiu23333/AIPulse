"""Bounded schema diagnostics: declared paths and fixed messages, never values."""
import json
import logging
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import ErrorType
from aipulse.runtime.logging import format_event

_MESSAGES = {
    'missing': 'Required field is missing.',
    'extra_forbidden': 'Unexpected field is not allowed.',
    'list_type': 'Expected an array.',
    'dict_type': 'Expected an object.',
    'model_type': 'Expected the declared model object.',
    'model_attributes_type': 'Expected a model object.',
    'string_type': 'Expected a string.',
    'int_type': 'Expected an integer.',
    'float_type': 'Expected a number.',
    'bool_type': 'Expected a boolean.',
    'literal_error': 'Value is outside the allowed constants.',
    'enum': 'Value is outside the allowed enumeration.',
    'string_pattern_mismatch': 'String does not match the required pattern.',
    'string_too_short': 'String is shorter than the declared minimum.',
    'string_too_long': 'String is longer than the declared maximum.',
    'too_short': 'Collection is shorter than the declared minimum.',
    'too_long': 'Collection is longer than the declared maximum.',
    'greater_than': 'Number is outside the declared range.',
    'greater_than_equal': 'Number is outside the declared range.',
    'less_than': 'Number is outside the declared range.',
    'less_than_equal': 'Number is outside the declared range.',
    'value_error': 'Field failed the application validation rule.',
    'validation_error': 'Field failed schema validation.',
}
_ERROR_TYPES = frozenset(get_args(ErrorType))

class SchemaIssue(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    loc: list[str | int] = Field(max_length=8)
    type: str = Field(max_length=64)
    message: str = Field(max_length=120)

class SchemaDiagnostic(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    stage: Literal['provider_output', 'backend_revalidation', 'report_conversion']
    schema_name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,80}$')
    issues: list[SchemaIssue] = Field(min_length=1, max_length=10)
    total_errors: int = Field(ge=1)
    truncated: bool

def schema_diagnostic(error: ValidationError, schema: type[BaseModel], stage: str) -> SchemaDiagnostic:
    names = set()
    def collect(value):
        if isinstance(value, dict):
            names.update(value.get('properties', {}))
            for child in value.values(): collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)
    collect(schema.model_json_schema())
    issues = []
    # Never call str(error), errors() defaults or serialize ctx/msg/input. Even
    # an extra field's key and a custom validator message can contain secrets.
    for item in error.errors(include_input=False, include_context=False, include_url=False)[:10]:
        loc = [part if type(part) is int and 0 <= part <= 999 else
               part if isinstance(part, str) and part in names else '[unknown_field]'
               for part in item['loc'][:8]]
        kind = item['type'] if item['type'] in _ERROR_TYPES else 'validation_error'
        issues.append(SchemaIssue(loc=loc, type=kind, message=_MESSAGES.get(kind, _MESSAGES['validation_error'])))
    diagnostic = SchemaDiagnostic(stage=stage, schema_name=schema.__name__, issues=issues,
                                  total_errors=error.error_count(), truncated=error.error_count()>len(issues))
    record = json.loads(format_event('schema_validation_failed', error_code='schema_mismatch'))
    record['schema_diagnostic'] = diagnostic.model_dump()
    logging.getLogger(__name__).warning('%s', json.dumps(record, ensure_ascii=False, separators=(',', ':')))
    return diagnostic
