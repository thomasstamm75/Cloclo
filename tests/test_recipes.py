import json
import tempfile
import unittest
from pathlib import Path

from cloclo import recipes
from cloclo.extract import Options


class RecipesTest(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.fichier = Path(self.dossier.name, "recipes.json")
        self.fichier.write_text(
            json.dumps(
                {
                    "recipes": [
                        {"match": "exemple.fr/actu", "item": ".actu", "title": "h3"},
                        {"match": "re:^https://[a-z]+\\.presse\\.fr/", "item": ".dep"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.addCleanup(self.dossier.cleanup)

    def test_correspondance_par_sous_chaine(self):
        recette = recipes.find("https://exemple.fr/actu/2026", self.fichier)
        self.assertEqual(recette["item"], ".actu")

    def test_correspondance_par_expression_reguliere(self):
        recette = recipes.find("https://afp.presse.fr/fil", self.fichier)
        self.assertEqual(recette["item"], ".dep")

    def test_aucune_correspondance(self):
        self.assertIsNone(recipes.find("https://autre.fr/", self.fichier))

    def test_application_sans_ecraser_les_options_explicites(self):
        options = recipes.apply(
            {"item": ".actu", "title": "h3"}, Options(title="h2.custom")
        )
        self.assertEqual(options.item, ".actu")
        self.assertEqual(options.title, "h2.custom")

    def test_fichier_illisible_ignore(self):
        mauvais = Path(self.dossier.name, "casse.json")
        mauvais.write_text("{ pas du json", encoding="utf-8")
        self.assertIsNone(recipes.find("https://exemple.fr/actu", mauvais))

    def test_fichier_fourni_valide(self):
        self.assertIsInstance(recipes.load(), list)


if __name__ == "__main__":
    unittest.main()
