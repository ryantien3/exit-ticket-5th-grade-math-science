"""Synthetic seed content for local development and demos.

Descriptions are short plain-language paraphrases, not the official standard text.
Each question's wrong choices are based on common student mistakes.
Every "key" is a stable name the seed script turns into a fixed UUID, so changing
text and re-running the seed updates rows instead of duplicating them.
"""

STANDARDS = [
    {
        "code": "NY-5.NF.1",
        "domain": "NY-5.NF",
        "subject": "math",
        "description": "Add and subtract fractions with unlike denominators (including mixed "
        "numbers) by rewriting them with a common denominator.",
    },
    {
        "code": "NY-5.NF.4",
        "domain": "NY-5.NF",
        "subject": "math",
        "description": "Multiply a fraction by a whole number or by another fraction.",
    },
    {
        "code": "NY-5.NBT.3",
        "domain": "NY-5.NBT",
        "subject": "math",
        "description": "Read, write, and compare decimals to thousandths.",
    },
    {
        "code": "NY-5.NBT.4",
        "domain": "NY-5.NBT",
        "subject": "math",
        "description": "Round decimals to any place.",
    },
    {
        "code": "NY-5.NBT.7",
        "domain": "NY-5.NBT",
        "subject": "math",
        "description": "Add, subtract, multiply, and divide decimals to hundredths.",
    },
    {
        "code": "5-PS1-1",
        "domain": "5-PS1",
        "subject": "science",
        "description": "Matter is made of particles too small to be seen.",
    },
    {
        "code": "5-PS1-2",
        "domain": "5-PS1",
        "subject": "science",
        "description": "When matter is heated, cooled, or mixed, its total weight stays the same.",
    },
    {
        "code": "5-PS1-3",
        "domain": "5-PS1",
        "subject": "science",
        "description": "Materials can be identified by their properties.",
    },
    {
        "code": "5-PS1-4",
        "domain": "5-PS1",
        "subject": "science",
        "description": "Mixing two or more substances can sometimes make a new substance.",
    },
]

QUESTIONS = [
    # ---------- NY-5.NF.1: add and subtract fractions with unlike denominators ----------
    {
        "key": "nf1-1",
        "standard": "NY-5.NF.1",
        "prompt": "What is 1/2 + 1/3?",
        "choices": ["2/5", "5/6", "2/6", "1/6"],
        "correct": "B",
        "explanation": "You can't just add the bottom numbers. First make the pieces the same "
        "size: 1/2 = 3/6 and 1/3 = 2/6. Then 3/6 + 2/6 = 5/6.",
    },
    {
        "key": "nf1-2",
        "standard": "NY-5.NF.1",
        "prompt": "What is 3/4 - 1/8?",
        "choices": ["2/4", "2/8", "5/8", "7/8"],
        "correct": "C",
        "explanation": "Change 3/4 into eighths: 3/4 = 6/8. Now both fractions have the same "
        "bottom number, so 6/8 - 1/8 = 5/8.",
    },
    {
        "key": "nf1-3",
        "standard": "NY-5.NF.1",
        "prompt": "Maya ran 2/3 of a mile in the morning and 1/4 of a mile after school. "
        "How far did she run in all?",
        "choices": ["11/12 of a mile", "3/7 of a mile", "3/12 of a mile", "2/12 of a mile"],
        "correct": "A",
        "explanation": "Twelfths work for both fractions: 2/3 = 8/12 and 1/4 = 3/12. "
        "8/12 + 3/12 = 11/12 of a mile.",
    },
    {
        "key": "nf1-4",
        "standard": "NY-5.NF.1",
        "prompt": "Jada has 5/6 of a pizza. She eats 1/3 of a pizza. How much pizza is left?",
        "choices": ["4/3 of a pizza", "4/6 of a pizza", "7/6 of a pizza", "1/2 of a pizza"],
        "correct": "D",
        "explanation": "Change 1/3 into sixths: 1/3 = 2/6. Then 5/6 - 2/6 = 3/6, which is "
        "the same as 1/2 of a pizza.",
    },
    {
        "key": "nf1-5",
        "standard": "NY-5.NF.1",
        "prompt": "To add 3/4 + 1/6, Leo rewrites both fractions with a denominator of 12. "
        "Which is correct?",
        "choices": ["3/12 + 1/12", "3/10 + 1/10", "9/12 + 2/12", "9/12 + 1/12"],
        "correct": "C",
        "explanation": "Whatever you multiply the bottom by, multiply the top by the same "
        "number. 3/4 = 9/12 (times 3) and 1/6 = 2/12 (times 2).",
    },
    # ---------- NY-5.NF.4: multiply fractions ----------
    {
        "key": "nf4-1",
        "standard": "NY-5.NF.4",
        "prompt": "What is 3 x 2/5?",
        "choices": ["6/15", "6/5", "3 2/5", "2/15"],
        "correct": "B",
        "explanation": "3 x 2/5 means 3 groups of 2/5: 2/5 + 2/5 + 2/5 = 6/5. Only the top "
        "number is multiplied, because the pieces are still fifths. 6/5 = 1 1/5.",
    },
    {
        "key": "nf4-2",
        "standard": "NY-5.NF.4",
        "prompt": "What is 2/3 x 3/4?",
        "choices": ["1/2", "5/7", "8/9", "9/8"],
        "correct": "A",
        "explanation": "Multiply top times top and bottom times bottom: (2 x 3)/(3 x 4) = "
        "6/12, which simplifies to 1/2.",
    },
    {
        "key": "nf4-3",
        "standard": "NY-5.NF.4",
        "prompt": "A recipe uses 3/4 cup of flour. Ali makes 1/2 of the recipe. "
        "How much flour does Ali use?",
        "choices": ["1 1/4 cups", "3/2 cups", "3/8 cup", "4/6 cup"],
        "correct": "C",
        "explanation": "Half of 3/4 means 1/2 x 3/4 = 3/8. Making half a recipe should use "
        "less flour, not more.",
    },
    {
        "key": "nf4-4",
        "standard": "NY-5.NF.4",
        "prompt": "What is 1/3 of 6?",
        "choices": ["18", "1/18", "6 1/3", "2"],
        "correct": "D",
        "explanation": "1/3 of 6 means 1/3 x 6 = 6/3 = 2. You can also split 6 into "
        "3 equal groups: each group has 2.",
    },
    {
        "key": "nf4-5",
        "standard": "NY-5.NF.4",
        "prompt": "A garden is 2/3 of a yard long and 1/2 of a yard wide. What is its area?",
        "choices": ["1/3 square yard", "3/5 square yard", "7/6 square yards", "4/3 square yards"],
        "correct": "A",
        "explanation": "Area = length x width = 2/3 x 1/2 = 2/6, which simplifies to "
        "1/3 square yard.",
    },
    # ---------- NY-5.NBT.3: read, write, and compare decimals ----------
    {
        "key": "nbt3-1",
        "standard": "NY-5.NBT.3",
        "prompt": "Which number is the greatest?",
        "choices": ["0.125", "0.45", "0.5", "0.09"],
        "correct": "C",
        "explanation": "More digits doesn't mean bigger. Compare the tenths place first: "
        "0.5 has 5 tenths, more than any of the others.",
    },
    {
        "key": "nbt3-2",
        "standard": "NY-5.NBT.3",
        "prompt": "How do you write \"three and forty-seven thousandths\" as a decimal?",
        "choices": ["3.47", "3.047", "3.0047", "0.347"],
        "correct": "B",
        "explanation": "\"Thousandths\" means the number ends in the thousandths place, "
        "3 places after the decimal point: 3.047.",
    },
    {
        "key": "nbt3-3",
        "standard": "NY-5.NBT.3",
        "prompt": "Which comparison is true?",
        "choices": ["0.40 = 0.4", "0.7 < 0.65", "0.302 > 0.32", "1.08 > 1.8"],
        "correct": "A",
        "explanation": "A zero at the end of a decimal doesn't change its value: 4 tenths "
        "and 40 hundredths are the same amount.",
    },
    {
        "key": "nbt3-4",
        "standard": "NY-5.NBT.3",
        "prompt": "In the number 4.256, what is the value of the digit 5?",
        "choices": ["0.5", "0.05", "0.005", "50"],
        "correct": "B",
        "explanation": "The places after the decimal point are tenths, hundredths, "
        "thousandths. The 5 is in the hundredths place, so its value is 0.05.",
    },
    {
        "key": "nbt3-5",
        "standard": "NY-5.NBT.3",
        "prompt": "Which shows 5.208 in expanded form?",
        "choices": [
            "5 + 0.2 + 0.08",
            "5 + 0.02 + 0.008",
            "5 + 2 + 0 + 8",
            "5 + 0.2 + 0.008",
        ],
        "correct": "D",
        "explanation": "5.208 has 5 ones, 2 tenths, 0 hundredths, and 8 thousandths: "
        "5 + 0.2 + 0.008.",
    },
    # ---------- NY-5.NBT.4: round decimals ----------
    {
        "key": "nbt4-1",
        "standard": "NY-5.NBT.4",
        "prompt": "Round 3.46 to the nearest tenth.",
        "choices": ["3.4", "3.5", "3.0", "4"],
        "correct": "B",
        "explanation": "Look at the digit to the right of the tenths place: 6. Since 6 is 5 "
        "or more, round up from 3.4 to 3.5.",
    },
    {
        "key": "nbt4-2",
        "standard": "NY-5.NBT.4",
        "prompt": "Round 7.825 to the nearest hundredth.",
        "choices": ["7.82", "7.8", "7.83", "7.9"],
        "correct": "C",
        "explanation": "The digit after the hundredths place is 5, so round up: 7.82 "
        "becomes 7.83. Just cutting off the last digit isn't rounding.",
    },
    {
        "key": "nbt4-3",
        "standard": "NY-5.NBT.4",
        "prompt": "Round 12.951 to the nearest whole number.",
        "choices": ["13", "12", "12.9", "12.95"],
        "correct": "A",
        "explanation": "For the nearest whole number, look at the tenths digit: 9. Since 9 "
        "is 5 or more, 12 rounds up to 13.",
    },
    {
        "key": "nbt4-4",
        "standard": "NY-5.NBT.4",
        "prompt": "A pencil is 14.38 centimeters long. What is its length rounded to the "
        "nearest tenth of a centimeter?",
        "choices": ["14.3 cm", "14 cm", "15 cm", "14.4 cm"],
        "correct": "D",
        "explanation": "The digit after the tenths place is 8, so round up: 14.3 becomes 14.4.",
    },
    {
        "key": "nbt4-5",
        "standard": "NY-5.NBT.4",
        "prompt": "Which number rounds to 6.2 when rounded to the nearest tenth?",
        "choices": ["6.14", "6.29", "6.17", "6.25"],
        "correct": "C",
        "explanation": "6.17 is between 6.1 and 6.2, and the 7 tells you to round up to 6.2. "
        "6.29 starts with 6.2, but it rounds up to 6.3.",
    },
    # ---------- NY-5.NBT.7: operations with decimals ----------
    {
        "key": "nbt7-1",
        "standard": "NY-5.NBT.7",
        "prompt": "What is 4.5 + 2.37?",
        "choices": ["2.82", "6.87", "6.42", "68.7"],
        "correct": "B",
        "explanation": "Line up the decimal points, not the last digits. Think of 4.5 as "
        "4.50: 4.50 + 2.37 = 6.87.",
    },
    {
        "key": "nbt7-2",
        "standard": "NY-5.NBT.7",
        "prompt": "What is 8.2 - 3.45?",
        "choices": ["5.25", "4.85", "4.75", "11.65"],
        "correct": "C",
        "explanation": "Write 8.2 as 8.20 so both numbers have hundredths. Then regroup: "
        "8.20 - 3.45 = 4.75. Don't flip the digits to subtract the smaller from the bigger.",
    },
    {
        "key": "nbt7-3",
        "standard": "NY-5.NBT.7",
        "prompt": "What is 0.3 x 0.2?",
        "choices": ["0.06", "0.6", "0.5", "6"],
        "correct": "A",
        "explanation": "3 tenths of 2 tenths is a small amount: 3 x 2 = 6, and tenths times "
        "tenths makes hundredths, so the answer is 0.06.",
    },
    {
        "key": "nbt7-4",
        "standard": "NY-5.NBT.7",
        "prompt": "Sam buys 3 notebooks that cost $1.25 each. How much does he spend?",
        "choices": ["$4.25", "$37.50", "$0.375", "$3.75"],
        "correct": "D",
        "explanation": "3 x $1.25 = $1.25 + $1.25 + $1.25 = $3.75. Check that your answer "
        "makes sense: 3 notebooks at about $1 each is about $3.",
    },
    {
        "key": "nbt7-5",
        "standard": "NY-5.NBT.7",
        "prompt": "4 friends share $9.20 equally. How much does each friend get?",
        "choices": ["$2.03", "$23.00", "$2.30", "$5.20"],
        "correct": "C",
        "explanation": "$9.20 / 4 = $2.30. Check by multiplying: 4 x $2.30 = $9.20.",
    },
    # ---------- 5-PS1-1: matter is made of tiny particles ----------
    {
        "key": "ps1-1-1",
        "standard": "5-PS1-1",
        "prompt": "Kai stirs a spoonful of sugar into a glass of water. The sugar seems to "
        "disappear. What really happened to it?",
        "choices": [
            "The sugar was destroyed.",
            "The sugar broke into particles too small to see, and they spread through the water.",
            "The sugar turned into water.",
            "The sugar floated away into the air.",
        ],
        "correct": "B",
        "explanation": "The sugar is still there. Its particles are just too small to see. "
        "You could prove it by tasting the water: it's sweet!",
    },
    {
        "key": "ps1-1-2",
        "standard": "5-PS1-1",
        "prompt": "When you pump air into a flat basketball, it gets bigger and firmer. "
        "What does this show?",
        "choices": [
            "Air is made of particles that take up space.",
            "Air has no weight.",
            "The rubber in the ball is growing.",
            "Air is not matter.",
        ],
        "correct": "A",
        "explanation": "You can't see air, but it is matter. Its tiny particles fill the ball "
        "and push on its sides.",
    },
    {
        "key": "ps1-1-3",
        "standard": "5-PS1-1",
        "prompt": "You can smell cookies baking from across the room. Which is the best "
        "explanation?",
        "choices": [
            "Smells are not made of matter.",
            "The cookies get bigger until they reach your nose.",
            "Tiny particles from the cookies travel through the air to your nose.",
            "You are smelling the heat from the oven.",
        ],
        "correct": "C",
        "explanation": "Tiny particles leave the cookies and spread through the air. When "
        "they reach your nose, you smell them, even though you can't see them.",
    },
    {
        "key": "ps1-1-4",
        "standard": "5-PS1-1",
        "prompt": "Why can't you see the particles that make up a glass of water?",
        "choices": [
            "Water has no particles; it is one solid piece of water.",
            "The particles only appear when water freezes.",
            "The particles sink to the bottom of the glass.",
            "The particles are far too small to see, even with a magnifying glass.",
        ],
        "correct": "D",
        "explanation": "All matter, including water, is made of particles. They are much too "
        "small to see with your eyes or a magnifying glass.",
    },
    {
        "key": "ps1-1-5",
        "standard": "5-PS1-1",
        "prompt": "Which of these is NOT matter?",
        "choices": ["Air inside a balloon", "Steam from a pot", "A shadow", "Dust"],
        "correct": "C",
        "explanation": "Matter is anything that has weight and takes up space. Air, steam, "
        "and dust are all made of particles. A shadow is just a place where light is blocked.",
    },
    # ---------- 5-PS1-2: weight is conserved ----------
    {
        "key": "ps1-2-1",
        "standard": "5-PS1-2",
        "prompt": "Some water in a closed container weighs 250 grams. The water freezes into "
        "ice. How much does the ice weigh?",
        "choices": ["More than 250 grams", "Less than 250 grams", "250 grams", "0 grams"],
        "correct": "C",
        "explanation": "Ice takes up a little more space than water, but nothing was added or "
        "taken away. The weight stays 250 grams.",
    },
    {
        "key": "ps1-2-2",
        "standard": "5-PS1-2",
        "prompt": "Rosa has 100 grams of water and 20 grams of salt. She stirs the salt into "
        "the water until it dissolves. How much does the salt water weigh?",
        "choices": ["100 grams", "120 grams", "80 grams", "110 grams"],
        "correct": "B",
        "explanation": "The salt is still there, even though you can't see it. "
        "100 grams + 20 grams = 120 grams.",
    },
    {
        "key": "ps1-2-3",
        "standard": "5-PS1-2",
        "prompt": "A sealed bag holds some vinegar and a small cup of baking soda. The bag "
        "weighs 300 grams. Pat tips the cup so they mix, and the bag puffs up with gas. "
        "Nothing leaks out. What does the bag weigh now?",
        "choices": [
            "300 grams",
            "More than 300 grams, because gas was made",
            "Less than 300 grams, because gas is light",
            "It depends on how much the bag puffs up",
        ],
        "correct": "A",
        "explanation": "The gas was made from the vinegar and baking soda that were already "
        "in the bag. Nothing got in or out, so the weight stays 300 grams.",
    },
    {
        "key": "ps1-2-4",
        "standard": "5-PS1-2",
        "prompt": "Jordan heats 200 grams of water in an open pot. Later, the water weighs "
        "180 grams. What happened to the missing 20 grams?",
        "choices": [
            "The heat destroyed it.",
            "Heating makes water lighter.",
            "It soaked into the pot.",
            "It turned into water vapor and went into the air.",
        ],
        "correct": "D",
        "explanation": "Matter doesn't disappear. Some water evaporated and left the open "
        "pot as water vapor. With a tight lid, the total weight would stay the same.",
    },
    {
        "key": "ps1-2-5",
        "standard": "5-PS1-2",
        "prompt": "When matter is heated, cooled, or mixed, and nothing is added or allowed "
        "to escape, what happens to its total weight?",
        "choices": [
            "The total weight goes up.",
            "The total weight goes down.",
            "The total weight stays the same.",
            "Solids get heavier, but liquids get lighter.",
        ],
        "correct": "C",
        "explanation": "Matter can change form, but the particles are all still there. "
        "So the total weight stays the same.",
    },
    # ---------- 5-PS1-3: identify materials by their properties ----------
    {
        "key": "ps1-3-1",
        "standard": "5-PS1-3",
        "prompt": "Two metal blocks are the same size and color. One is iron and one is "
        "aluminum. Which test would best tell them apart?",
        "choices": [
            "Compare their shapes",
            "Check whether a magnet attracts each one",
            "Measure their sizes",
            "See if they are shiny",
        ],
        "correct": "B",
        "explanation": "A magnet attracts iron but not aluminum. Shape and size don't help "
        "here, because the blocks are the same size.",
    },
    {
        "key": "ps1-3-2",
        "standard": "5-PS1-3",
        "prompt": "Leo has two clear liquids. Sugar dissolves in Liquid 1 but not in "
        "Liquid 2. What can Leo conclude?",
        "choices": [
            "The liquids are different materials.",
            "The liquids are the same material.",
            "Liquid 2 is heavier.",
            "Both liquids are water.",
        ],
        "correct": "A",
        "explanation": "Different materials have different properties. Because the liquids "
        "act differently with sugar, they can't be the same material.",
    },
    {
        "key": "ps1-3-3",
        "standard": "5-PS1-3",
        "prompt": "Which object is the best conductor of electricity?",
        "choices": ["A rubber band", "A wooden craft stick", "A copper wire", "A plastic spoon"],
        "correct": "C",
        "explanation": "Metals like copper let electricity flow through them easily. Rubber, "
        "wood, and plastic do not, which is why they are used to cover wires.",
    },
    {
        "key": "ps1-3-4",
        "standard": "5-PS1-3",
        "prompt": "Which observation describes a property that could help identify a "
        "material?",
        "choices": [
            "It is on the kitchen table.",
            "It is the biggest object in the room.",
            "It is 5 centimeters long.",
            "It does not dissolve in water.",
        ],
        "correct": "D",
        "explanation": "Where an object is, or how big it is, doesn't tell you what it's made "
        "of. Whether a material dissolves in water is a property of the material itself.",
    },
    {
        "key": "ps1-3-5",
        "standard": "5-PS1-3",
        "prompt": "Mia has four white powders: sugar, salt, baking soda, and cornstarch. "
        "Which test would help her find the baking soda?",
        "choices": [
            "Add a few drops of vinegar and watch for fizzing.",
            "Look at the color of each powder.",
            "Put each powder in the same size cup.",
            "Measure how much space a spoonful takes up.",
        ],
        "correct": "A",
        "explanation": "All four powders are white, so color won't help. Baking soda is the "
        "only one of the four that fizzes when vinegar touches it.",
    },
    # ---------- 5-PS1-4: mixing can make a new substance ----------
    {
        "key": "ps1-4-1",
        "standard": "5-PS1-4",
        "prompt": "Which is a sign that mixing two things made a new substance?",
        "choices": [
            "Bubbles of gas form when baking soda and vinegar are mixed.",
            "Sand sinks to the bottom when it is mixed with water.",
            "Salt disappears when it is stirred into water.",
            "The mixture is wet.",
        ],
        "correct": "A",
        "explanation": "A new gas forming is a sign of a new substance. Sand sinking and salt "
        "dissolving don't make anything new; the sand and salt are still there.",
    },
    {
        "key": "ps1-4-2",
        "standard": "5-PS1-4",
        "prompt": "Ava mixes iron filings with sand. Did she make a new substance?",
        "choices": [
            "Yes, because they are now mixed together.",
            "No, because she can pull the iron out with a magnet and both keep their properties.",
            "Yes, because the color of the pile changed.",
            "No, because sand is heavier than iron.",
        ],
        "correct": "B",
        "explanation": "In a mixture, each material keeps its own properties and can be "
        "separated again. The iron is still magnetic and the sand is still sand.",
    },
    {
        "key": "ps1-4-3",
        "standard": "5-PS1-4",
        "prompt": "When salt is stirred into water, is a new substance made?",
        "choices": [
            "Yes, because the salt is gone.",
            "Yes, because salty water is something new.",
            "No. The salt is still there; if the water evaporates, the salt is left behind.",
            "No, because nothing happens to the water.",
        ],
        "correct": "C",
        "explanation": "Dissolving is not the same as making a new substance. Let the water "
        "evaporate and the salt comes back.",
    },
    {
        "key": "ps1-4-4",
        "standard": "5-PS1-4",
        "prompt": "Two clear liquids are mixed. Which observation would show a new substance "
        "was made?",
        "choices": [
            "The mixture needs a bigger cup.",
            "The mixture looks like the two liquids stirred together.",
            "The mixture is the same temperature as before.",
            "A cloudy solid appears that was not in either liquid.",
        ],
        "correct": "D",
        "explanation": "A solid appearing from two liquids means something new formed. Other "
        "signs are a color change, gas bubbles, or getting hotter or colder.",
    },
    {
        "key": "ps1-4-5",
        "standard": "5-PS1-4",
        "prompt": "When milk and vinegar are mixed, white clumps form. Why do scientists say "
        "a new substance was made?",
        "choices": [
            "There is more liquid now.",
            "The milk got colder.",
            "The clumps have properties different from both the milk and the vinegar.",
            "The vinegar disappeared.",
        ],
        "correct": "C",
        "explanation": "A new substance has new properties. The rubbery clumps are not like "
        "milk or vinegar, so something new was made.",
    },
]

# Each ticket lists question keys in the order students see them.
EXIT_TICKETS = [
    {
        "key": "fractions-add-subtract",
        "title": "Adding & Subtracting Fractions",
        "subject": "math",
        "questions": ["nf1-1", "nf1-2", "nf1-3", "nf1-4"],
    },
    {
        "key": "fractions-multiply",
        "title": "Multiplying Fractions",
        "subject": "math",
        "questions": ["nf4-1", "nf4-2", "nf4-3"],
    },
    {
        "key": "decimals-place-value-rounding",
        "title": "Decimal Place Value & Rounding",
        "subject": "math",
        "questions": ["nbt3-1", "nbt3-3", "nbt4-1", "nbt4-2", "nbt4-5"],
    },
    {
        "key": "decimals-operations",
        "title": "Decimal Operations",
        "subject": "math",
        "questions": ["nbt7-1", "nbt7-2", "nbt7-3", "nbt7-4"],
    },
    {
        "key": "matter-particles-conservation",
        "title": "Particles & Conservation of Matter",
        "subject": "science",
        "questions": ["ps1-1-1", "ps1-1-2", "ps1-2-1", "ps1-2-2"],
    },
    {
        "key": "matter-properties-mixing",
        "title": "Properties & Mixing",
        "subject": "science",
        "questions": ["ps1-3-1", "ps1-3-5", "ps1-4-1", "ps1-4-3"],
    },
]

CLASSROOM_NAME = "Class 5-201"

# Synthetic roster: common first names, each paired with a generated last initial.
# Not real people.
FIRST_NAMES = [
    "Avery", "Blake", "Carmen", "Dev", "Elena", "Felix", "Grace", "Hiro",
    "Isla", "Jalen", "Kira", "Luis", "Maya", "Nico", "Omar", "Priya",
    "Quinn", "Rosa", "Sam", "Tariq", "Uma", "Victor", "Wren", "Xavi",
]  # fmt: skip


def student_display_names() -> list[str]:
    # Step through the alphabet 7 letters at a time; 7 and 26 share no factors,
    # so every student gets a different last initial.
    return [f"{first} {chr(ord('A') + (i * 7 + 3) % 26)}." for i, first in enumerate(FIRST_NAMES)]
