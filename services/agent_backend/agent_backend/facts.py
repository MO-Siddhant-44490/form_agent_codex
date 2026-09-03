"""Hard-coded Slice 1 fact set (replaced by document intelligence in Slice 2).

Every fact is user_provided: hard-coded test data is by definition not
extracted from a document, and provenance rules require a source for
extracted facts (invariant 11).
"""

from form_contracts import DocumentFact, FactStatus, FactValueType, Sensitivity


def slice1_facts() -> list[DocumentFact]:
    def fact(
        key: str,
        value: str,
        value_type: FactValueType,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
    ) -> DocumentFact:
        return DocumentFact(
            fact_id=f"fact-{key.replace('_', '-')}",
            key=key,
            value=value,
            value_type=value_type,
            confidence=1.0,
            sensitivity=sensitivity,
            status=FactStatus.USER_PROVIDED,
        )

    return [
        fact("full_name", "Ada Lovelace", FactValueType.STRING),
        fact("email", "ada@example.test", FactValueType.EMAIL),
        fact("phone", "+1 555 010 2030", FactValueType.PHONE),
        fact("date_of_birth", "1998-04-17", FactValueType.DATE),
        fact("country", "IN", FactValueType.ENUM, Sensitivity.PUBLIC),
        fact("years_experience", "5", FactValueType.NUMBER, Sensitivity.PUBLIC),
        fact("contact_method", "email", FactValueType.ENUM, Sensitivity.PUBLIC),
        fact("subscribe", "true", FactValueType.BOOLEAN, Sensitivity.PUBLIC),
    ]
