import unittest

from app.platform_core.schema_names import SchemaName, schema_for_domain


class SchemaNameTests(unittest.TestCase):
    def test_supported_non_education_domains(self):
        self.assertEqual(schema_for_domain("medical"), SchemaName.MEDICAL)
        self.assertEqual(schema_for_domain("legal"), SchemaName.LEGAL)
        self.assertEqual(schema_for_domain("life"), SchemaName.LIFE)

    def test_education_is_not_remapped_by_new_platform_layer(self):
        with self.assertRaises(ValueError):
            schema_for_domain("education")


if __name__ == "__main__":
    unittest.main()
