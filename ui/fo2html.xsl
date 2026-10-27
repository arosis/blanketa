<?xml version="1.0" encoding="UTF-8"?>
<!-- Corrections on top of zfo-editor's FO→HTML stylesheet; templates here take precedence over the imported ones. -->
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:fo="http://www.w3.org/1999/XSL/Format"
                xmlns:fm="http://software602.cz/forms"
                exclude-result-prefixes="fo fm">

  <xsl:import href="../vendor/zfo-editor-src/zfo-editor-0.3.2/xslt/fo2html.xsl"/>

  <!-- Embedded picture: real image type from its signature, size from the enclosing fo:instream-foreign-object. -->
  <xsl:template match="fm:internal-graphic">
    <xsl:variable name="data" select="normalize-space(text())"/>
    <xsl:variable name="type">
      <xsl:choose>
        <xsl:when test="starts-with($data, 'iVBOR')">png</xsl:when>
        <xsl:when test="starts-with($data, 'R0lG')">gif</xsl:when>
        <xsl:when test="starts-with($data, 'Qk')">bmp</xsl:when>
        <xsl:otherwise>jpeg</xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <xsl:variable name="box" select="ancestor::fo:instream-foreign-object[1]"/>
    <img src="data:image/{$type};base64,{$data}" alt="">
      <xsl:attribute name="style">
        <xsl:text>display: inline-block; max-width: 100%; object-fit: contain;</xsl:text>
        <xsl:if test="$box/@width"> width: <xsl:value-of select="$box/@width"/>;</xsl:if>
        <xsl:if test="$box/@height"> height: <xsl:value-of select="$box/@height"/>;</xsl:if>
      </xsl:attribute>
    </img>
  </xsl:template>

</xsl:stylesheet>
