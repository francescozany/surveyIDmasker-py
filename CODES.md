# How the codes are made

Each identifier is **encrypted**, not hashed or replaced by a random number,
so the authors, who keep the key, can turn the codes back into the original
values.

## The key

A key file holds one random 256-bit secret (from `openssl::rand_bytes`). Two
separate keys are derived from it with HMAC-SHA256:

- an encryption key, `HMAC(secret, "surveyIDmasker-encrypt")`;
- a check key, `HMAC(secret, "surveyIDmasker-check")`.

## From a value to a code

1. The value (as UTF-8 text) is padded to at least 18 bytes: a `0x80` byte,
   then zeros (ISO/IEC 7816-4 padding). Short IDs then give codes as long as
   longer ones, so length reveals little.
2. A tag is computed: the first 8 bytes of `HMAC-SHA256(check key, padded value)`.
3. The padded value is encrypted with **AES-256 in CTR mode**, using the tag
   (followed by 8 zero bytes) as the starting counter.
4. Tag and encrypted bytes are written in base 62 (`A-Z`, `a-z`, `0-9`) after
   a `0x01` byte, so codes contain only letters and digits.
5. `MSK` goes in front. When the country/state of a location or IP address is
   kept, it goes between `MSK` and `_`: `MSKIT_...`, `MSKUS-CT_...`.

This follows the idea of the standard "synthetic IV" mode (AES-SIV, RFC 5297),
built from the AES and HMAC functions available in R.

Latitude and longitude are encrypted together as one pair, with a marker for
which half each column holds, so the same place gives the same pair of codes.

## What this means

- **Equal values give equal codes**, with the same key. This is what makes
  repeat participants recognizable, in one file and across files.
- **Without the key, a code cannot be turned back** into the value, and
  codes made with one key say nothing about codes made with another.
- **With the key, every code can be turned back** (`reidentify()`). The tag
  also checks that a code was made with that key, so a wrong key is
  detected instead of giving wrong values.
- **Anyone who has the key file can re-identify the data.** Keep it with the
  original data, never with the shared files.
- Equal codes reveal that two cells hold the same value (e.g. the same IP
  address). Keeping the country/state reveals that region.

## Test values

With the example secret made of the bytes `00 01 02 ... 1f` (never use it for
real data), any implementation of the steps above must give:

| Value | Code |
|---|---|
| `A1B2C3D4E5F6G7` | `MSKyfXOdQODgzfNar1ztKlXIxotIGBQXbGkUnY` |
| `5f1a2b3c4d5e6f7a8b9c0d1e` | `MSKEDme9TFVDrLzRv1RI8HmkzQ10wBQUKnn3yLef0TkMUta1` |
| `1.2.3.4` | `MSKBDmEGynURZQHc6ap3tiajChhCXT9lalAfmL0` |
